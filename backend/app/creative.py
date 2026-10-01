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

Video lives in `veo.py` (Google Veo via the Gemini API) since OpenAI shut the
Sora API down on 2026-09-24.
"""

from __future__ import annotations

import base64
import io
import json
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

# The 2.5 family is the only one whose `quality` goes above "high" (to
# "xhigh"/"max"), per the installed SDK's own docs -- and this key has it.
DEFAULT_IMAGE_MODEL = "gpt-image-2.5-flare"

IMAGE_MODELS = [
    "gpt-image-2.5-flare",
    "gpt-image-2.5-sunburst",
    "gpt-image-2",
    "gpt-image-1.5",
    "gpt-image-1",
]
MAX_QUALITY_MODELS = {
    "gpt-image-2.5-flare", "gpt-image-2.5-flare-2026-09-08",
    "gpt-image-2.5-sunburst", "gpt-image-2.5-sunburst-2026-09-08",
}


def quality_options(model: str) -> list[str]:
    """Valid `quality` values for a model, best last."""
    if model in MAX_QUALITY_MODELS:
        return ["low", "medium", "high", "xhigh", "max"]
    return ["low", "medium", "high"]


def best_quality(model: str) -> str:
    return quality_options(model)[-1]


# Dashboard (Ulting) logos -- fallbacks only. Creatives are for ULTEx, so a
# file dropped into brand/ always wins over these.
LOGO_CANDIDATES = ["ulting-wordmark.png", "ulting-logo.png", "ulting-mark.png"]
LOGO_DARK = "ulting-wordmark-dark.png"
LOGO_CACHE = OUTPUT_DIR.parent / "logo-cache"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


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


def _brand_logos() -> list[str]:
    if not BRAND_DIR.exists():
        return []
    return [p.name for p in sorted(BRAND_DIR.iterdir()) if p.suffix.lower() in IMAGE_SUFFIXES]


def available_logos() -> list[str]:
    """Brand files first -- they are the company's real logo -- then fallbacks."""
    fallbacks = [n for n in [*LOGO_CANDIDATES, LOGO_DARK] if (PUBLIC_DIR / n).exists()]
    return _brand_logos() + fallbacks


def _logo_path(name: str | None = None) -> Path | None:
    if name:
        for base in (PUBLIC_DIR, BRAND_DIR):
            candidate = base / name
            # Contain to the two brand directories: the name comes from the
            # client, so "../../.env.local" must not resolve.
            if candidate.exists() and base.resolve() in candidate.resolve().parents:
                return candidate
        return None
    brand = _brand_logos()
    if brand:
        return BRAND_DIR / brand[0]
    for candidate in LOGO_CANDIDATES:
        if (PUBLIC_DIR / candidate).exists():
            return PUBLIC_DIR / candidate
    return None


REFERENCES_DIR = BRAND_DIR / "references"
MAX_REFERENCES = 4
DESIGN_MODES = ["full_ad", "visual"]


def style_references(limit: int = MAX_REFERENCES) -> list[Path]:
    """Past ULTEx ads that define the house design system.

    Sent to the image model next to the logo so a new ad comes out in the same
    family -- layout, typography, palette, finish -- instead of the model's
    generic idea of "an ad". Drop more into brand/references/ to change it.
    """
    if not REFERENCES_DIR.exists():
        return []
    files = [p for p in sorted(REFERENCES_DIR.iterdir()) if p.suffix.lower() in IMAGE_SUFFIXES]
    return files[:limit]


def _reference_bytes(path: Path) -> tuple[str, bytes, str]:
    """A reference re-encoded as JPEG at ~1024px.

    The originals are ~1.5-2MB PNGs; four of them plus the logo is a slow
    upload on every generation, and the model only needs the design, not the
    pixels at full size.
    """
    im = Image.open(path).convert("RGB")
    im.thumbnail((1024, 1024), Image.LANCZOS)
    buffer = io.BytesIO()
    im.save(buffer, format="JPEG", quality=88)
    return (f"{path.stem}.jpg", buffer.getvalue(), "image/jpeg")


def brand_kit() -> dict[str, Any]:
    default_model = get_settings().openai_image_model or DEFAULT_IMAGE_MODEL
    default_logo = _logo_path()
    return {
        "personaConfigured": bool(load_persona()),
        "personaPath": str(PERSONA_FILE.relative_to(REPO_ROOT)),
        "logos": available_logos(),
        "defaultLogo": default_logo.name if default_logo else None,
        "references": [p.name for p in style_references()],
        "designModes": DESIGN_MODES,
        "imageModels": IMAGE_MODELS,
        "defaultImageModel": default_model,
        "qualityByModel": {m: quality_options(m) for m in IMAGE_MODELS},
        "logoModes": ["model", "overlay", "none"],
        "imageSizes": list(IMAGE_SIZES),
    }


LOGO_POSITION_WORDS = {
    "bottom-right": "in the bottom-right corner",
    "bottom-left": "in the bottom-left corner",
    "top-right": "in the top-right corner",
    "top-left": "in the top-left corner",
    "bottom-center": "centred along the bottom edge",
    "auto": "wherever it reads best in the composition",
}


def compose_prompt(
    user_prompt: str,
    *,
    kind: str,
    use_persona: bool = True,
    logo_mode: str = "none",
    logo_position: str = "auto",
    design: str = "full_ad",
    n_references: int = 0,
) -> str:
    """Wrap the user's idea in the brand direction.

    For images there are two designs. `full_ad` is a finished poster -- logo,
    headline, subline, CTA, optional benefit band -- matching the reference
    ads. `visual` is the picture alone, for when the layout is done elsewhere.

    When images are attached, their order is spelled out: the model is told
    which one is the logo to reproduce and which are style references to learn
    from but never copy. Without that it blends them -- copying a reference's
    dominoes, or restyling the logo to match a reference.
    """
    parts: list[str] = []
    persona = load_persona() if use_persona else ""
    if persona:
        parts.append(f"Brand direction to follow:\n{persona}")

    if kind != "image":
        parts.append(
            "Produce a short vertical advertising video clip. Keep the composition "
            "simple enough to read on a phone. Any on-screen text is limited to a short "
            "end card. Do not render any logo or watermark."
        )
        parts.append(f"The creative to make:\n{user_prompt.strip()}")
        return "\n\n".join(parts)

    with_logo = logo_mode == "model"
    image_roles = []
    index = 1
    if with_logo:
        image_roles.append(
            f"Image {index} is the official ULTEx logo, cut out on a transparent "
            "background. Reproduce it exactly: same letterforms, arrow, blue and yellow "
            "colours, proportions and tagline. Never redraw, restyle, recolour or "
            "misspell it, and never put it on a white box."
        )
        index += 1
    if n_references:
        last = index + n_references - 1
        span = f"Image {index}" if n_references == 1 else f"Images {index}-{last}"
        image_roles.append(
            f"{span}: previous ULTEx ads. They define the house design system - copy "
            "their layout grammar, typography, colour palette, light background, "
            "premium studio finish and level of polish closely. Do NOT copy their "
            "visual concept, objects or wording: this is a new ad."
        )

    if design == "visual":
        parts.append(
            "Produce the visual only - a single, high-end advertising image with no "
            "text, no words, no letters, no buttons and no benefit band; it will be "
            "laid out later. Leave calm negative space for a headline."
            + (" Do not draw any logo." if not with_logo else "")
        )
    else:
        where = LOGO_POSITION_WORDS.get(logo_position, "in the top-left corner")
        if logo_position == "auto":
            where = "in the top-left corner (top-right if the visual needs the left side)"
        parts.append(
            "Produce a finished, ready-to-publish Meta advertisement: a designed "
            "poster, not a bare photo. Layout, top to bottom: "
            + (f"the ULTEx logo {where}; " if with_logo else
               "clean space in the top-left corner where the logo will be added - do not draw any logo; ")
            + "one strong visual metaphor as the hero, in a premium studio-lit 3D or "
            "photographic style on a light background; a big bold two-line headline in "
            "a geometric sans-serif, navy, with the punchline heavier or in yellow; a "
            "thin yellow rule; one short subline; a CTA with an arrow (navy circle with "
            "white arrow, or a yellow pill button). Add a navy benefit band with 3-4 "
            "line icons and short uppercase labels only if the brief asks for it or "
            "it clearly helps.\n"
            "Text: render every word exactly as written in the brief, in French, with "
            "correct accents and no extra words. If the brief does not give exact "
            "wording, write short French copy in the house tone: a two-beat headline, "
            "one subline, a CTA. Keep text minimal and perfectly legible. No other "
            "logos, no watermarks, no placeholder or lorem-ipsum text."
        )
    if image_roles:
        parts.append("Attached images:\n- " + "\n- ".join(image_roles))
    parts.append(f"The ad to make:\n{user_prompt.strip()}")
    return "\n\n".join(parts)


# ------------------------------------------------------------- logo preparation

def remove_logo_background(source: Path, *, tolerance: int = 38) -> Path:
    """Cut the logo out of its background into a transparent PNG.

    Logos usually arrive as JPEGs on white, and a JPEG has no alpha: pasted or
    handed to the model as-is it becomes a white rectangle.

    Two steps. First a flood fill from the image border marks the background,
    so only background *connected to the edge* is removed -- white enclosed
    inside the mark survives. Then the edge is un-blended rather than eroded:
    each pixel next to the background gets an alpha from how far its colour is
    from the background, and its colour is un-mixed from that background. That
    removes the white halo JPEG leaves around every letter without thinning
    the strokes -- an erode did, and it broke the small tagline apart.

    Files that already carry real transparency are trimmed and passed through.
    Results are cached by name, size and mtime, so this runs once per file.
    """
    import numpy as np
    from PIL import ImageChops, ImageDraw, ImageFilter

    stat = source.stat()
    LOGO_CACHE.mkdir(parents=True, exist_ok=True)
    cached = LOGO_CACHE / f"{source.stem}-{stat.st_size}-{int(stat.st_mtime)}-v3.png"
    if cached.exists():
        return cached

    rgba = Image.open(source).convert("RGBA")
    already_transparent = rgba.getchannel("A").getextrema()[0] < 250

    if not already_transparent:
        rgb = rgba.convert("RGB")
        w, h = rgb.size
        # Background colour from the border, so off-white and light-grey
        # backdrops work as well as pure white.
        border = [rgb.getpixel((x, y)) for x in range(0, w, max(1, w // 40)) for y in (0, h - 1)]
        border += [rgb.getpixel((x, y)) for y in range(0, h, max(1, h // 40)) for x in (0, w - 1)]
        bg = np.array([sorted(c[i] for c in border)[len(border) // 2] for i in range(3)], float)

        # 1. Background region: near-background pixels connected to the border.
        distance = ImageChops.difference(rgb, Image.new("RGB", rgb.size, tuple(int(v) for v in bg)))
        near = [band.point(lambda v: 255 if v <= tolerance else 0) for band in distance.split()]
        mask = ImageChops.multiply(ImageChops.multiply(near[0], near[1]), near[2])
        seeds = [(x, y) for x in range(w) for y in (0, h - 1)]
        seeds += [(x, y) for y in range(h) for x in (0, w - 1)]
        for seed in seeds:
            if mask.getpixel(seed) == 255:
                ImageDraw.floodfill(mask, seed, 128)
        background = np.array(mask) == 128

        # 2. Soft edge: pixels within 2px of the background region.
        bg_img = Image.fromarray((background * 255).astype("uint8"))
        band = (np.array(bg_img.filter(ImageFilter.MaxFilter(5))) > 0) & ~background

        pixels = np.array(rgb, float)
        # How far each pixel is from the background, 0..1, on its furthest channel.
        span = np.maximum(np.where(bg > 127, bg, 255 - bg), 1)
        dist = np.max(np.abs(pixels - bg) / span, axis=2)

        alpha = np.full((h, w), 1.0)
        alpha[background] = 0.0
        # Brand blue sits ~0.96 from white on its furthest channel; scaling by
        # ~1/0.96 makes a fully-coloured pixel exactly opaque, so partial pixels
        # get their true coverage and the un-mix below removes all the white.
        # A larger boost (1.6 was tried) left a pale fringe on dark backdrops.
        alpha[band] = np.clip(dist[band] * 1.05, 0.0, 1.0)

        # Un-mix the background out of the edge colours: c = (o - (1-a)*bg) / a.
        a = alpha[..., None]
        clean = np.where(a > 0.02, (pixels - (1 - a) * bg) / np.maximum(a, 0.02), pixels)
        clean = np.clip(clean, 0, 255)

        out = np.dstack([clean, alpha * 255]).astype("uint8")
        rgba = Image.fromarray(out, "RGBA")

    bbox = rgba.getchannel("A").getbbox()
    if bbox:
        pad = 8
        bbox = (max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                min(rgba.width, bbox[2] + pad), min(rgba.height, bbox[3] + pad))
        rgba = rgba.crop(bbox)

    rgba.save(cached, format="PNG")
    return cached


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
    # Only the Ulting wordmark has a light-on-dark twin. Swapping anything else
    # (the ULTEx logo, say) would replace the client's logo with another brand.
    if not dark_variant.exists() or logo_file.name not in LOGO_CANDIDATES:
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
    quality: str | None = None,
    logo: str | None = None,
    logo_mode: str = "model",
    logo_position: str = "auto",
    logo_scale: float = 0.16,
    use_persona: bool = True,
    model: str | None = None,
    design: str = "full_ad",
    use_references: bool = True,
) -> dict[str, Any]:
    """One branded image.

    `design`: "full_ad" (default) is a finished poster with headline, subline,
    CTA and logo, styled on the reference ads in brand/references/; "visual"
    is the picture alone.

    `logo_mode`:
      - "model"   (default) the background-stripped logo is sent to the image
                  model and it places that exact logo in the design
      - "overlay" generated without a logo, the real file pasted on after --
                  pixel-exact, for when the model distorts the letters
      - "none"    no logo
    """
    client = _client()
    settings = get_settings()
    model = model or settings.openai_image_model or DEFAULT_IMAGE_MODEL
    quality = quality if quality in quality_options(model) else best_quality(model)
    dimensions = IMAGE_SIZES.get(size, IMAGE_SIZES["square"])
    design = design if design in DESIGN_MODES else "full_ad"

    source = _logo_path(logo) if logo_mode != "none" and logo != "none" else None
    cutout = remove_logo_background(source) if source else None
    mode = logo_mode if cutout else "none"
    references = style_references() if use_references else []

    full_prompt = compose_prompt(
        prompt,
        kind="image",
        use_persona=use_persona,
        logo_mode="model" if mode == "model" else "none",
        logo_position=logo_position,
        design=design,
        n_references=len(references),
    )

    inputs: list[tuple[str, bytes, str]] = []
    if mode == "model":
        inputs.append(("logo.png", cutout.read_bytes(), "image/png"))
    inputs += [_reference_bytes(r) for r in references]

    if inputs:
        payload = await _edit_with_images(client, model, inputs, full_prompt, dimensions, quality)
    else:
        response = await client.images.generate(
            model=model, prompt=full_prompt, size=dimensions, quality=quality, n=1,
        )
        payload = response.data[0]

    raw = base64.b64decode(payload.b64_json) if payload.b64_json else None
    if raw is None:
        raise RuntimeError("Image API returned no image data")

    if mode == "overlay":
        image = Image.open(io.BytesIO(raw))
        default_corner = "top-left" if design == "full_ad" else "bottom-right"
        position = logo_position if logo_position != "auto" else default_corner
        image = _place_logo(image, cutout, position, logo_scale)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        raw = buffer.getvalue()

    meta = _write(raw, prompt, ".png")
    return {
        **meta,
        "kind": "image",
        "prompt": prompt,
        "revisedPrompt": getattr(payload, "revised_prompt", None),
        "size": dimensions,
        "model": model,
        "quality": quality,
        "design": design,
        "references": [r.name for r in references],
        "logo": source.name if source else None,
        "logoMode": mode,
        "personaApplied": bool(use_persona and load_persona()),
    }


async def _edit_with_images(client, model: str, inputs, prompt: str, size: str, quality: str):
    """images.edit with the logo and/or reference ads as input images.

    `input_fidelity="high"` keeps the output close to the inputs -- which is
    what keeps the logo's letterforms intact. Some models reject or ignore it
    (the SDK docs say gpt-image-2 ignores it), so a rejection naming the
    parameter retries once without it instead of failing the generation.
    """
    async def call(with_fidelity: bool):
        kwargs = dict(model=model, image=list(inputs), prompt=prompt, size=size,
                      quality=quality, n=1)
        if with_fidelity:
            kwargs["input_fidelity"] = "high"
        return await client.images.edit(**kwargs)

    try:
        response = await call(True)
    except openai.BadRequestError as error:
        if "input_fidelity" not in str(error):
            raise
        response = await call(False)
    return response.data[0]


def gallery(limit: int = 60) -> list[dict[str, Any]]:
    """Everything generated so far, newest first."""
    if not OUTPUT_DIR.exists():
        return []
    files = sorted(
        (p for p in OUTPUT_DIR.iterdir() if p.suffix.lower() in {".png", ".mp4"}),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    items = []
    for p in files[:limit]:
        item = {
            "file": p.name,
            "url": f"/api/creative/asset/{p.name}",
            "kind": "video" if p.suffix.lower() == ".mp4" else "image",
            "bytes": p.stat().st_size,
            "createdAt": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(),
        }
        # Veo videos carry a sidecar (see veo.py) -- the page needs it to know
        # whether a video can be extended, and how long it already is.
        sidecar = p.with_name(p.name + ".json")
        if item["kind"] == "video" and sidecar.exists():
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
                item["veo"] = {k: meta.get(k) for k in (
                    "provider", "model", "aspect", "resolution", "seconds", "extensions", "parent",
                    "prompt")}
            except (OSError, json.JSONDecodeError):
                pass
        items.append(item)
    return items


def asset_path(name: str) -> Path | None:
    """Resolve a generated file, refusing anything outside the output directory."""
    candidate = (OUTPUT_DIR / name).resolve()
    if OUTPUT_DIR.resolve() not in candidate.parents or not candidate.exists():
        return None
    return candidate
