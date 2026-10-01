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
    style_references,
)
from ..veo import VeoError, extend_video, start_video, video_kit, video_status

router = APIRouter(prefix="/api/creative", tags=["creative"], dependencies=[Depends(require_session)])


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    size: str = "square"
    # None -> the best the chosen model supports ("max" on the 2.5 family).
    quality: str | None = None
    model: str | None = None
    logo: str | None = None
    # "model": logo sent to the image model as an input image (default).
    # "overlay": pasted on afterwards, pixel-exact. "none": no logo.
    logo_mode: str = Field(default="model", pattern="^(model|overlay|none)$")
    logo_position: str = "auto"
    logo_scale: float = Field(default=0.16, ge=0.04, le=0.5)
    use_persona: bool = True
    # full_ad: finished poster with headline/CTA in the house style; visual: picture only.
    design: str = Field(default="full_ad", pattern="^(full_ad|visual)$")
    use_references: bool = True


class ExtendRequest(BaseModel):
    # The gallery file to continue; it must be a Veo video made here.
    source: str = Field(min_length=5, max_length=300)
    prompt: str = Field(min_length=3, max_length=2000)
    model: str | None = None


class IdeasRequest(BaseModel):
    account_id: str | None = None
    brief: str = Field(default="", max_length=2000)
    count: int = Field(default=4, ge=1, le=8)
    use_trends: bool = True
    audience: str = Field(default="", max_length=200)
    service: str = Field(default="", max_length=200)
    language: str = Field(default="fr", pattern=r"^(fr|darija|ar|fr\+darija)$")
    placement: str = Field(default="", max_length=60)
    # Video length in scenes: 8s, then +7s per Veo extension.
    video_scenes: int = Field(default=1, ge=1, le=8)
    preset: str | None = None
    since: str | None = None
    until: str | None = None


class VideoRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    size: str = Field(default="portrait", pattern="^(portrait|landscape)$")
    seconds: str = Field(default="8", pattern="^(4|6|8)$")
    resolution: str = Field(default="720p", pattern="^(720p|1080p)$")
    model: str | None = None
    # A generated image (gallery file name) to use as the first frame.
    start_image: str | None = None
    use_persona: bool = True


def _handle(cause: Exception) -> HTTPException:
    if isinstance(cause, CreativeNotConfigured):
        return HTTPException(status_code=503, detail={"message": str(cause)})
    if isinstance(cause, VeoError):
        return HTTPException(
            status_code=400 if cause.status == 400 else 502,
            detail={"message": f"Veo error ({cause.status}): {cause.message}"},
        )
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
    return {**brand_kit(), **video_kit()}


@router.post("/preview-prompt")
def preview_prompt(body: ImageRequest):
    """What the model will actually receive, so the persona is never a mystery."""
    return {
        "prompt": compose_prompt(
            body.prompt,
            kind="image",
            use_persona=body.use_persona,
            logo_mode="model" if body.logo_mode == "model" and body.logo != "none" else "none",
            logo_position=body.logo_position,
            design=body.design,
            n_references=len(style_references()) if body.use_references else 0,
        )
    }


@router.post("/image")
async def image(body: ImageRequest):
    try:
        return await generate_image(
            body.prompt,
            size=body.size,
            quality=body.quality,
            model=body.model,
            logo=body.logo,
            logo_mode=body.logo_mode,
            logo_position=body.logo_position,
            logo_scale=body.logo_scale,
            use_persona=body.use_persona,
            design=body.design,
            use_references=body.use_references,
        )
    except Exception as cause:  # noqa: BLE001 - mapped to a status the UI can show
        raise _handle(cause) from cause


@router.post("/video")
async def video(body: VideoRequest):
    """Start a Veo render. It takes a minute or more, so this returns a job to poll."""
    try:
        return await start_video(
            body.prompt, size=body.size, seconds=body.seconds, resolution=body.resolution,
            model=body.model, start_image=body.start_image, use_persona=body.use_persona,
        )
    except Exception as cause:  # noqa: BLE001
        raise _handle(cause) from cause


@router.post("/video/extend")
async def video_extend(body: ExtendRequest):
    """Continue a Veo video by 7 seconds. Returns a job to poll like /video."""
    try:
        return await extend_video(body.source, body.prompt, model=body.model)
    except Exception as cause:  # noqa: BLE001
        raise _handle(cause) from cause


@router.get("/video/{video_id:path}")
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
            snapshot=snapshot, audit=audit, account_id=body.account_id,
            brief=body.brief, count=body.count, use_trends=body.use_trends,
            audience=body.audience, service=body.service,
            language=body.language, placement=body.placement,
            video_scenes=body.video_scenes,
        )
    except PromptGenNotConfigured as cause:
        raise HTTPException(status_code=503, detail={"message": str(cause)}) from cause
    except Exception as cause:  # noqa: BLE001
        raise _handle(cause) from cause


@router.get("/logo-preview/{name}")
def logo_preview(name: str):
    """The background-removed version of a logo -- exactly what gets sent."""
    from ..creative import _logo_path, remove_logo_background

    source = _logo_path(name)
    if not source:
        raise HTTPException(status_code=404, detail={"message": "Logo not found"})
    return FileResponse(remove_logo_background(source), media_type="image/png")


@router.get("/reference/{name}")
def reference(name: str):
    """A house-style reference ad from brand/references/, for the UI strip."""
    match = next((p for p in style_references() if p.name == name), None)
    if not match:
        raise HTTPException(status_code=404, detail={"message": "Reference not found"})
    return FileResponse(match)


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
