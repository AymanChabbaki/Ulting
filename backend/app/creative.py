"""
Creative generator: images and video from a prompt, in the brand's voice.

Three decisions worth knowing about.

**The logo is composited, not drawn.** Image models garble logos and small
type — asking one to "put the Ulting logo in the corner" reliably produces a
smeared approximation of it. So the image is generated clean and the real PNG
is pasted on afterwards with Pillow, which is pixel-exact and free. Passing the
logo as a reference image (`images.edit`) is offered too, but it restyles the
logo rather than reproducing it, so it is not the default.

**The persona is a file, not a constant.** `brand/persona.md` is re-read on
every request, so the brand direction can be edited without a restart or a
deploy.

**Video is a job, not a request.** Sora renders take minutes, so the route
starts the job and the client polls. Nothing blocks an HTTP worker for the
duration of a render.
"""

from __future__ import annotations

import base64
import io
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import openai
from PIL import Image

from .config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
BRAND_DIR = REPO_ROOT / "brand"
PERSONA_FILE = BRAND_DIR / "persona.md"
PUBLIC_DIR = REPO_ROOT / "frontend" / "public"
OUTPUT_DIR = Path(os.environ.get("ULTING_DATA_DIR", REPO_ROOT / "backend")) / "generated"

# Exact values from the installed SDK's enums -- not guesses.
IMAGE_SIZES = {"square": "1024x1024", "portrait": "1024x1536", "landscape": "1536x1024"}
VIDEO_SIZES = {"portrait": "720x1280", "landscape": "1280x720"}
VIDEO_SECONDS = ("4", "8", "12")

DEFAULT_IMAGE_MODEL = "gpt-image-1"
DEFAULT_VIDEO_MODEL = "sora-2"

# Logo files, best first. The dark wordmark reads on dark imagery, the mark
# alone survives being small.
LOGO_CANDIDATES = ["ulting-wordmark.png", "ulting-logo.png", "ulting-mark.png"]
LOGO_DARK = "ulting-wordmark-dark.png"


class CreativeNotConfigured(RuntimeError):
    pass


def _client() -> openai.AsyncOpenAI:
    key = get_settings().openai_api_key
    try:
        return openai.AsyncOpenAI(api_key=key) if key else openai.AsyncOpenAI()
    except Exception as cause:  # noqa: BLE001 - surfaced as guidance
        raise CreativeNotConfigured(
            "No OpenAI credentials found. Set OPENAI_API_KEY in .env.local and restart."
        ) from cause


# ------------------------------------------------------------------- brand kit

def load_persona() -> str:
    """The persona text, re-read every call so edits take effect immediately."""
    if not PERSONA_FILE.exists():
        return ""
    text = PERSONA_FILE.read_text(encoding="utf-8")
    # Drop the "edit this file" preamble above the --- rule; it is instructions
    # to the human and would otherwise be read as instructions to the model.
    if "\n---\n" in text:
        text = text.split("\n---\n", 1)[1]
    return text.strip()


def available_logos() -> list[str]:
    found = [n for n in [*LOGO_CANDIDATES, LOGO_DARK] if (PUBLIC_DIR / n).exists()]
    # Anything the user drops into brand/ counts too.
    if BRAND_DIR.exists():
        found += [
            p.name for p in sorted(BRAND_DIR.iterdir())
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ]
    return found


def _logo_path(name: str | None = None) -> Path | None:
    if name:
        for base in (PUBLIC_DIR, BRAND_DIR):
            candidate = base / name
            # Contain to the two brand directories: the name comes from the
            # client, so "../../.env.local" must not resolve.
            if candidate.exists() and base.resolve() in candidate.resolve().parents:
                return candidate
        return None
    for candidate in LOGO_CANDIDATES:
        if (PUBLIC_DIR / candidate).exists():
            return PUBLIC_DIR / candidate
    return None


def brand_kit() -> dict[str, Any]:
    return {
        "personaConfigured": bool(load_persona()),
        "personaPath": str(PERSONA_FILE.relative_to(REPO_ROOT)),
        "logos": available_logos(),
        "imageSizes": list(IMAGE_SIZES),
        "videoSizes": list(VIDEO_SIZES),
        "videoSeconds": list(VIDEO_SECONDS),
    }


def compose_prompt(user_prompt: str, *, kind: str, use_persona: bool = True) -> str:
    """Wrap the user's idea in the brand direction.

    The user's own words go last. Persona first means the model reads the
    constraints before the subject, and the subject is what it holds onto.
    """
    parts: list[str] = []
    persona = load_persona() if use_persona else ""
    if persona:
        parts.append(f"Brand direction to follow:\n{persona}")
    if kind == "image":
        parts.append(
            "Produce a single still advertising image. Leave clean, uncluttered "
            "space in one corner where a logo will be placed afterwards. Do not "
            "draw any logo, watermark or brand name."
        )
    else:
        parts.append(
            "Produce a short advertising video clip. Keep the composition simple "
            "enough to read on a phone. Do not render any logo or watermark."
        )
    parts.append(f"The creative to make:\n{user_prompt.strip()}")
    return "\n\n".join(parts)


# ------------------------------------------------------------------ compositing

def _slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:limit] or "creative").rstrip("-")


def _region_is_dark(canvas: Image.Image, box: tuple[int, int, int, int]) -> bool:
    """Mean perceived luminance of the area the logo will cover."""
    crop = canvas.convert("RGB").crop(box)
    if crop.width == 0 or crop.height == 0:
        return False
    # Downsample to one pixel: Pillow averages for us, and the mean is all the
    # decision needs.
    r, g, b = crop.resize((1, 1), Image.BOX).getpixel((0, 0))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 128


def _pick_logo_variant(canvas: Image.Image, logo_file: Path, box: tuple) -> Path:
    """Swap to the light-on-dark wordmark when the backdrop is dark.

    The standard wordmark's letters are brand blue, which disappears against
    dark imagery -- and logistics creative is full of dusk ports and shadowed
    warehouses, so this is the common case, not the edge case. Only the
    auto-selected default is swapped; an explicitly chosen file is honoured.
    """
    dark_variant = PUBLIC_DIR / LOGO_DARK
    if not dark_variant.exists() or logo_file.name == LOGO_DARK:
        return logo_file
    return dark_variant if _region_is_dark(canvas, box) else logo_file


def _place_logo(
    base: Image.Image,
    logo_file: Path,
    position: str = "bottom-right",
    scale: float = 0.16,
    auto_variant: bool = True,
) -> Image.Image:
    """Paste the real logo onto a generated image.

    Pixel-exact by construction: this is the actual PNG, not a model's
    impression of it. Width is a fraction of the canvas so the logo reads the
    same at every output size, and the margin scales with it.
    """
    canvas = base.convert("RGBA")
    logo = Image.open(logo_file).convert("RGBA")

    target_w = max(48, int(canvas.width * scale))
    target_h = max(1, round(logo.height * (target_w / logo.width)))
    logo = logo.resize((target_w, target_h), Image.LANCZOS)

    margin = max(16, int(canvas.width * 0.035))
    positions = {
        "bottom-right": (canvas.width - target_w - margin, canvas.height - target_h - margin),
        "bottom-left": (margin, canvas.height - target_h - margin),
        "top-right": (canvas.width - target_w - margin, margin),
        "top-left": (margin, margin),
        "bottom-center": ((canvas.width - target_w) // 2, canvas.height - target_h - margin),
    }
    xy = positions.get(position, positions["bottom-right"])
    box = (xy[0], xy[1], xy[0] + target_w, xy[1] + target_h)

    if auto_variant:
        chosen = _pick_logo_variant(canvas, logo_file, box)
        if chosen != logo_file:
            logo = Image.open(chosen).convert("RGBA").resize((target_w, target_h), Image.LANCZOS)

    # Paste through the logo's own alpha so transparency is preserved.
    canvas.alpha_composite(logo, xy)
    return canvas


# -------------------------------------------------------------------- generation

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _write(data: bytes, prompt: str, suffix: str) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{_slug(prompt)}-{uuid.uuid4().hex[:6]}{suffix}"
    (OUTPUT_DIR / name).write_bytes(data)
    return {
        "file": name,
        "url": f"/api/creative/asset/{name}",
        "bytes": len(data),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }


async def generate_image(
    prompt: str,
    *,
    size: str = "square",
    quality: str = "high",
    logo: str | None = None,
    logo_position: str = "bottom-right",
    logo_scale: float = 0.16,
    use_persona: bool = True,
    reference_logo: bool = False,
    model: str | None = None,
) -> dict[str, Any]:
    """One image. Logo composited afterwards unless `reference_logo` is set."""
    client = _client()
    settings = get_settings()
    full_prompt = compose_prompt(prompt, kind="image", use_persona=use_persona)
    dimensions = IMAGE_SIZES.get(size, IMAGE_SIZES["square"])
    model = model or settings.openai_image_model or DEFAULT_IMAGE_MODEL

    logo_file = _logo_path(logo) if logo != "none" else None

    if reference_logo and logo_file:
        # The model restyles the logo rather than reproducing it -- offered
        # because it can look more integrated, but never the default.
        with logo_file.open("rb") as handle:
            response = await client.images.edit(
                model=model,
                image=handle,
                prompt=full_prompt + "\n\nIncorporate the supplied logo naturally.",
                size=dimensions,
                input_fidelity="high",
            )
    else:
        response = await client.images.generate(
            model=model, prompt=full_prompt, size=dimensions, quality=quality, n=1,
        )

    payload = response.data[0]
    raw = base64.b64decode(payload.b64_json) if payload.b64_json else None
    if raw is None:
        raise RuntimeError("Image API returned no image data")

    composited = False
    if logo_file and not reference_logo:
        image = Image.open(io.BytesIO(raw))
        image = _place_logo(image, logo_file, logo_position, logo_scale)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        raw = buffer.getvalue()
        composited = True

    meta = _write(raw, prompt, ".png")
    return {
        **meta,
        "kind": "image",
        "prompt": prompt,
        "revisedPrompt": getattr(payload, "revised_prompt", None),
        "size": dimensions,
        "model": model,
        "logo": logo_file.name if logo_file else None,
        "logoMode": "reference" if reference_logo else ("composited" if composited else "none"),
        "personaApplied": bool(use_persona and load_persona()),
    }


async def start_video(
    prompt: str,
    *,
    size: str = "portrait",
    seconds: str = "4",
    use_persona: bool = True,
    model: str | None = None,
) -> dict[str, Any]:
    """Kick off a render. Returns immediately -- Sora takes minutes."""
    client = _client()
    settings = get_settings()
    if seconds not in VIDEO_SECONDS:
        seconds = "4"
    video = await client.videos.create(
        model=model or settings.openai_video_model or DEFAULT_VIDEO_MODEL,
        prompt=compose_prompt(prompt, kind="video", use_persona=use_persona),
        size=VIDEO_SIZES.get(size, VIDEO_SIZES["portrait"]),
        seconds=seconds,
    )
    return {
        "id": video.id,
        "status": video.status,
        "progress": getattr(video, "progress", 0),
        "kind": "video",
        "prompt": prompt,
        "seconds": seconds,
        "size": VIDEO_SIZES.get(size, VIDEO_SIZES["portrait"]),
        "model": video.model,
    }


async def video_status(video_id: str) -> dict[str, Any]:
    """Poll a render, downloading it once it completes."""
    client = _client()
    video = await client.videos.retrieve(video_id)
    payload: dict[str, Any] = {
        "id": video.id,
        "status": video.status,
        "progress": getattr(video, "progress", 0) or 0,
        "seconds": video.seconds,
        "size": video.size,
        "model": video.model,
    }

    if video.status == "failed":
        error = getattr(video, "error", None)
        payload["error"] = getattr(error, "message", None) or str(error or "Render failed")
        return payload

    if video.status != "completed":
        return payload

    existing = sorted(OUTPUT_DIR.glob(f"*{video.id[-8:]}.mp4"))
    if existing:
        name = existing[0].name
        payload.update({"file": name, "url": f"/api/creative/asset/{name}"})
        return payload

    content = await client.videos.download_content(video.id, variant="video")
    data = await content.aread()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    # The id tail is in the filename so a re-poll finds the existing download
    # instead of paying to fetch it again.
    name = f"{stamp}-{_slug(video.prompt or 'video')}-{video.id[-8:]}.mp4"
    (OUTPUT_DIR / name).write_bytes(data)
    payload.update({"file": name, "url": f"/api/creative/asset/{name}", "bytes": len(data)})
    return payload


def gallery(limit: int = 60) -> list[dict[str, Any]]:
    """Everything generated so far, newest first."""
    if not OUTPUT_DIR.exists():
        return []
    files = sorted(
        (p for p in OUTPUT_DIR.iterdir() if p.suffix.lower() in {".png", ".mp4"}),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [
        {
            "file": p.name,
            "url": f"/api/creative/asset/{p.name}",
            "kind": "video" if p.suffix.lower() == ".mp4" else "image",
            "bytes": p.stat().st_size,
            "createdAt": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(),
        }
        for p in files[:limit]
    ]


def asset_path(name: str) -> Path | None:
    """Resolve a generated file, refusing anything outside the output directory."""
    candidate = (OUTPUT_DIR / name).resolve()
    if OUTPUT_DIR.resolve() not in candidate.parents or not candidate.exists():
        return None
    return candidate
