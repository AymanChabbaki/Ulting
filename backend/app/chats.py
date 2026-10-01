"""
Saved strategist conversations.

One JSON file per chat under `$ULTING_DATA_DIR/chats/`, so they live on the
same Docker volume as generated creatives and the change log and survive a
redeploy. A file per chat keeps writes small and independent -- two tabs
talking to two chats never rewrite each other's history.

Each chat records the account and reporting window it was started on. The
answers are conclusions about that window's numbers, so the UI shows it
rather than letting an old thread pass for current analysis.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CHAT_DIR = Path(os.environ.get("ULTING_DATA_DIR", Path(__file__).resolve().parents[1])) / "chats"

_ID = re.compile(r"^[a-f0-9]{32}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(chat_id: str) -> Path | None:
    # The id is used in a file path -- only ever accept the exact shape we mint.
    return CHAT_DIR / f"{chat_id}.json" if _ID.match(chat_id or "") else None


def _title(text: str, limit: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def get_chat(chat_id: str) -> dict[str, Any] | None:
    path = _path(chat_id)
    if not path or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save(chat: dict[str, Any]) -> None:
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(chat["id"])
    # Write-then-rename: a crash mid-write leaves the old file, not half a file.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(chat, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def create_chat(*, account_id: str, account_name: str, window_label: str, first_message: str) -> dict[str, Any]:
    chat = {
        "id": uuid.uuid4().hex,
        "title": _title(first_message),
        "accountId": account_id,
        "accountName": account_name,
        "windowLabel": window_label,
        "createdAt": _now(),
        "updatedAt": _now(),
        "messages": [],
    }
    _save(chat)
    return chat


def append_exchange(chat_id: str, question: str, answer: str, tools: list[str], *, error: str | None = None) -> None:
    """Record one question and the reply it got -- even a partial or failed one,
    so a stopped answer is not silently dropped from the history."""
    chat = get_chat(chat_id)
    if chat is None:
        return
    chat["messages"].append({"role": "user", "content": question, "at": _now()})
    reply: dict[str, Any] = {"role": "assistant", "content": answer, "tools": tools, "at": _now()}
    if error:
        reply["error"] = error
    chat["messages"].append(reply)
    chat["updatedAt"] = _now()
    _save(chat)


def rename_chat(chat_id: str, title: str) -> dict[str, Any] | None:
    chat = get_chat(chat_id)
    if chat is None:
        return None
    chat["title"] = _title(title, 120) or chat["title"]
    _save(chat)
    return chat


def delete_chat(chat_id: str) -> bool:
    path = _path(chat_id)
    if not path or not path.exists():
        return False
    path.unlink()
    return True


def list_chats(account_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Summaries, newest first -- without the messages, which can be long."""
    if not CHAT_DIR.exists():
        return []
    summaries = []
    for path in CHAT_DIR.glob("*.json"):
        try:
            chat = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if account_id and chat.get("accountId") != account_id:
            continue
        summaries.append({
            k: chat.get(k)
            for k in ("id", "title", "accountId", "accountName", "windowLabel", "createdAt", "updatedAt")
        } | {"turns": sum(1 for m in chat.get("messages", []) if m.get("role") == "user")})
    summaries.sort(key=lambda c: c.get("updatedAt") or "", reverse=True)
    return summaries[:limit]
