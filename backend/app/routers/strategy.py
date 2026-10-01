"""Streaming chat with the strategist, and its saved conversations. Session-gated."""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..audit.engine import run_audit
from ..auth import require_session
from ..cache import audit_cache, breakdown_cache
from ..chats import append_exchange, create_chat, delete_chat, get_chat, list_chats, rename_chat
from ..meta.client import MetaApiError
from ..meta.collect import collect_account, fetch_breakdown, normalise_account_id
from ..meta.window import resolve_window
from ..strategist import StrategistNotConfigured, stream_reply
from .data import BREAKDOWNS

router = APIRouter(prefix="/api/strategy", tags=["strategy"], dependencies=[Depends(require_session)])

MAX_TURNS = 40


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    account_id: str
    messages: list[Turn]
    preset: str | None = None
    since: str | None = None
    until: str | None = None
    # Continue a saved chat; omitted on the first message, which creates one.
    chat_id: str | None = None


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.post("/chat")
async def chat(body: ChatRequest):
    """Server-sent events: thinking / text / tool / done / error."""
    if not body.messages or body.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail={"message": "The last message must be the question"})
    if body.chat_id and get_chat(body.chat_id) is None:
        raise HTTPException(status_code=404, detail={"message": "Chat not found"})

    try:
        window = resolve_window(body.preset, body.since, body.until)
    except ValueError as cause:
        raise HTTPException(status_code=400, detail={"message": str(cause)}) from cause

    # Share the audit cache: opening the chat right after viewing the dashboard
    # costs no Meta calls at all.
    try:
        snapshot, _ = await audit_cache.get_or_set(
            f"{body.account_id}:{window.key}",
            lambda: collect_account(body.account_id, window),
        )
    except MetaApiError as error:
        raise HTTPException(
            status_code=401 if error.is_auth_error else 502,
            detail={"message": error.message, "code": error.code},
        ) from error

    audit = run_audit(snapshot)

    async def breakdown_fetcher(cut: str):
        """The one tool that can reach Meta -- through the same 5-minute cache."""
        if cut not in BREAKDOWNS:
            return {"error": f"Unknown breakdown '{cut}'"}

        async def produce():
            async with httpx.AsyncClient(timeout=90.0) as client:
                return await fetch_breakdown(
                    normalise_account_id(body.account_id), window, BREAKDOWNS[cut], client
                )

        try:
            rows, _ = await breakdown_cache.get_or_set(
                f"{body.account_id}:{cut}:{window.key}", produce
            )
            return rows
        except MetaApiError as error:
            return {"error": error.message, "code": error.code}

    # Trim to the most recent turns. A long thread would otherwise resend the
    # whole history on every request, and the account context is re-supplied
    # each turn anyway.
    history = [t.model_dump() for t in body.messages[-MAX_TURNS:]]

    question = body.messages[-1].content
    chat_id = body.chat_id or create_chat(
        account_id=body.account_id,
        account_name=(snapshot.get("account") or {}).get("name") or body.account_id,
        window_label=snapshot.get("windowLabel") or window.key,
        first_message=question,
    )["id"]

    async def events() -> AsyncIterator[str]:
        answer: list[str] = []
        tools: list[str] = []
        error: str | None = None
        yield _sse({"type": "chat", "id": chat_id})
        try:
            async for event in stream_reply(
                history, snapshot=snapshot, audit=audit, breakdown_fetcher=breakdown_fetcher
            ):
                if event.get("type") == "text":
                    answer.append(event.get("text", ""))
                elif event.get("type") == "tool":
                    tools.append(event.get("name", ""))
                yield _sse(event)
        except StrategistNotConfigured as cause:
            error = str(cause)
            yield _sse({"type": "error", "message": error})
        except Exception as cause:  # noqa: BLE001 - the stream must close cleanly
            error = f"{type(cause).__name__}: {cause}"
            yield _sse({"type": "error", "message": error})
        finally:
            # Runs on a stopped or disconnected stream too, so whatever was
            # said before Stop is kept.
            append_exchange(chat_id, question, "".join(answer), tools, error=error)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Without this, nginx and friends buffer the whole stream and the
            # user sees nothing until the answer is complete.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/suggestions")
async def suggestions(account_id: str = Query(...)):
    """Opening prompts, so the empty chat is not a blank box."""
    return {
        "suggestions": [
            "What should I fix first, and what will it save?",
            "Which placements are wasting money?",
            "Is my budget split across ad sets hurting the learning phase?",
            "Which creatives are working and which should I kill?",
            "Are we paying more to reach men than women for the same result?",
            "Write me a plan for next week.",
        ]
    }


@router.get("/chats")
def chats(account_id: str | None = Query(None)):
    """Saved conversations, newest first. Filtered to one account when given."""
    return {"chats": list_chats(account_id)}


@router.get("/chats/{chat_id}")
def chat_detail(chat_id: str):
    chat = get_chat(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail={"message": "Chat not found"})
    return chat


@router.patch("/chats/{chat_id}")
def chat_rename(chat_id: str, body: RenameRequest):
    chat = rename_chat(chat_id, body.title)
    if chat is None:
        raise HTTPException(status_code=404, detail={"message": "Chat not found"})
    return {"id": chat["id"], "title": chat["title"]}


@router.delete("/chats/{chat_id}")
def chat_delete(chat_id: str):
    if not delete_chat(chat_id):
        raise HTTPException(status_code=404, detail={"message": "Chat not found"})
    return {"deleted": chat_id}
