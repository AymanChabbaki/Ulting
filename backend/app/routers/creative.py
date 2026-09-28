"""Creative generation routes. Session-gated like every other data route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import openai

from ..audit.engine import run_audit
from ..auth import require_session
from ..cache import audit_cache
from ..meta.collect import collect_account
from ..meta.window import resolve_window
from ..promptgen import PromptGenNotConfigured, generate_ideas
from ..creative import (
    CreativeNotConfigured,
    asset_path,
    brand_kit,
    compose_prompt,
    gallery,
    generate_image,
    start_video,
    video_status,
)

router = APIRouter(prefix="/api/creative", tags=["creative"], dependencies=[Depends(require_session)])


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    size: str = "square"
    quality: str = "high"
    logo: str | None = None
    logo_position: str = "bottom-right"
    logo_scale: float = Field(default=0.16, ge=0.04, le=0.5)
    use_persona: bool = True
    reference_logo: bool = False


class IdeasRequest(BaseModel):
    account_id: str | None = None
    brief: str = Field(default="", max_length=2000)
    count: int = Field(default=4, ge=1, le=8)
    use_trends: bool = True
    preset: str | None = None
    since: str | None = None
    until: str | None = None


class VideoRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    size: str = "portrait"
    seconds: str = "4"
    use_persona: bool = True


def _handle(cause: Exception) -> HTTPException:
    if isinstance(cause, CreativeNotConfigured):
        return HTTPException(status_code=503, detail={"message": str(cause)})
    if isinstance(cause, openai.APIStatusError):
        # Content-policy refusals arrive as 400s with a usable message; passing
        # it through beats a generic "generation failed".
        return HTTPException(
            status_code=502,
            detail={"message": f"OpenAI error ({cause.status_code}): {cause.message}"},
        )
    if isinstance(cause, openai.APIConnectionError):
        return HTTPException(status_code=502, detail={"message": f"Could not reach OpenAI: {cause}"})
    return HTTPException(status_code=500, detail={"message": f"{type(cause).__name__}: {cause}"})


@router.get("/brand")
def brand():
    """Persona status, available logos and the valid size/duration options."""
    return brand_kit()


@router.post("/preview-prompt")
def preview_prompt(body: ImageRequest):
    """What the model will actually receive, so the persona is never a mystery."""
    return {"prompt": compose_prompt(body.prompt, kind="image", use_persona=body.use_persona)}


@router.post("/image")
async def image(body: ImageRequest):
    try:
        return await generate_image(
            body.prompt,
            size=body.size,
            quality=body.quality,
            logo=body.logo,
            logo_position=body.logo_position,
            logo_scale=body.logo_scale,
            use_persona=body.use_persona,
            reference_logo=body.reference_logo,
        )
    except Exception as cause:  # noqa: BLE001 - mapped to a status the UI can show
        raise _handle(cause) from cause


@router.post("/video")
async def video(body: VideoRequest):
    """Start a render. Sora takes minutes, so this returns a job to poll."""
    try:
        return await start_video(
            body.prompt, size=body.size, seconds=body.seconds, use_persona=body.use_persona
        )
    except Exception as cause:  # noqa: BLE001
        raise _handle(cause) from cause


@router.get("/video/{video_id}")
async def video_poll(video_id: str):
    try:
        return await video_status(video_id)
    except Exception as cause:  # noqa: BLE001
        raise _handle(cause) from cause


@router.post("/ideas")
async def ideas(body: IdeasRequest):
    """Creative briefs with ready-to-use prompts, grounded in real performance."""
    snapshot = audit = None
    if body.account_id:
        try:
            window = resolve_window(body.preset, body.since, body.until)
            snapshot, _ = await audit_cache.get_or_set(
                f"{body.account_id}:{window.key}",
                lambda: collect_account(body.account_id, window),
            )
            audit = run_audit(snapshot)
        except Exception:  # noqa: BLE001
            # Ideas without account data are still useful -- the persona and
            # the trend search carry them. Better than failing the request.
            snapshot = audit = None
    try:
        return await generate_ideas(
            snapshot=snapshot, audit=audit, brief=body.brief,
            count=body.count, use_trends=body.use_trends,
        )
    except PromptGenNotConfigured as cause:
        raise HTTPException(status_code=503, detail={"message": str(cause)}) from cause
    except Exception as cause:  # noqa: BLE001
        raise _handle(cause) from cause


@router.get("/gallery")
def list_gallery(limit: int = Query(60, ge=1, le=200)):
    return {"assets": gallery(limit)}


@router.get("/asset/{name}")
def asset(name: str):
    path = asset_path(name)
    if not path:
        raise HTTPException(status_code=404, detail={"message": "Asset not found"})
    media = "video/mp4" if path.suffix.lower() == ".mp4" else "image/png"
    return FileResponse(path, media_type=media)
