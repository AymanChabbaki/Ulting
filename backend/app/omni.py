"""
Video with Gemini Omni Flash, through the Gemini Interactions API.

A different shape from Veo (see veo.py), so it lives on its own:

- one `POST /v1beta/interactions` call per clip, with the video asked for in
  `response_format` (aspect ratio, resolution, delivery by uri)
- no seconds parameter: a clip is ~5-10s and its timing follows the prompt
- extending continues the *interaction* (`previous_interaction_id` + task
  "extend"), up to 10s per turn and 40s in total
- no negative prompt -- the exclusions are written into the prompt instead

The call can hold the connection for the whole render, so it runs as a
background task on the server and the page polls a job id ("omni-...")
exactly as it does for Veo. Jobs live in memory: if the server restarts
mid-render the job is lost, but anything already finished is in the gallery
with its sidecar, and can still be extended.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
import uuid
from typing import Any

import httpx

from . import veo

OMNI_MODELS = ["gemini-omni-1.1-flash", "gemini-omni-flash-preview"]
OMNI_RESOLUTIONS = ["360p", "720p", "1080p", "4k"]
EXTEND_SECONDS = 10
MAX_TOTAL_SECONDS = 40
RENDER_TIMEOUT = 15 * 60

_TERMINAL = {"completed", "failed", "cancelled", "canceled", "incomplete", "expired"}

# job id -> {"status", "meta", "error", "file", "seconds", "task"}
_TASKS: dict[str, dict[str, Any]] = {}

EXCLUSIONS = (
    "Avoid: chaotic or damaged cargo, dark gloomy scenes, stock-photo handshakes, "
    "neon glow, distorted or invented logos, misspelled text, watermarks."
)


def _format(aspect: str, resolution: str) -> dict[str, Any]:
    return {"type": "video", "aspect_ratio": aspect, "resolution": resolution, "delivery": "uri"}


def _launch(meta: dict[str, Any], body: dict[str, Any]) -> str:
    job = f"omni-{uuid.uuid4().hex}"
    entry: dict[str, Any] = {"status": "in_progress", "meta": meta, "started": time.time()}
    _TASKS[job] = entry
    # Keep a reference: an un-referenced task can be garbage-collected mid-run.
    entry["task"] = asyncio.create_task(_run(job, body))
    return job


async def start(
    prompt: str,
    *,
    model: str,
    aspect: str,
    resolution: str,
    start_image: str | None,
    use_persona: bool,
) -> dict[str, Any]:
    veo._key()
    resolution = resolution if resolution in OMNI_RESOLUTIONS else "720p"
    image = veo._start_image(start_image) if start_image else None
    text = veo.video_prompt(prompt, use_persona=use_persona, start_image=bool(image)) + "\n\n" + EXCLUSIONS
    content: Any = text
    if image:
        content = [
            {"type": "image", "data": image["data"], "mime_type": image["mime"]},
            {"type": "text", "text": text},
        ]
    body = {"model": model, "input": content, "response_format": _format(aspect, resolution)}
    meta = {
        "provider": "omni", "model": model, "aspect": aspect, "resolution": resolution,
        "prompt": prompt, "rootPrompt": prompt, "startImage": start_image if image else None,
        "parent": None, "extensions": 0,
    }
    job = _launch(meta, body)
    return {"id": job, "status": "queued", "progress": 0, "kind": "video", "prompt": prompt,
            "aspect": aspect, "resolution": resolution, "model": model}


async def extend(source: str, prompt: str, meta: dict[str, Any]) -> dict[str, Any]:
    veo._key()
    interaction = meta.get("interaction")
    if not interaction:
        raise veo.VeoError(400, "This Omni video has no interaction id, so it cannot be extended.")
    length = meta.get("seconds")
    if length and length >= MAX_TOTAL_SECONDS:
        raise veo.VeoError(400, f"Omni Flash extends videos up to {MAX_TOTAL_SECONDS}s in total; this one is {length:.0f}s.")
    model = meta.get("model") or OMNI_MODELS[0]
    body = {
        "model": model,
        "previous_interaction_id": interaction,
        "input": veo.extension_prompt(prompt) + "\n\n" + EXCLUSIONS,
        "generation_config": {"video_config": {"task": "extend"}},
        "response_format": _format(meta.get("aspect") or "9:16", meta.get("resolution") or "720p"),
    }
    new_meta = {
        "provider": "omni", "model": model, "aspect": meta.get("aspect"),
        "resolution": meta.get("resolution"), "prompt": prompt,
        "rootPrompt": meta.get("rootPrompt") or meta.get("prompt") or prompt,
        "startImage": meta.get("startImage"), "parent": source,
        "extensions": (meta.get("extensions") or 0) + 1,
    }
    job = _launch(new_meta, body)
    return {"id": job, "status": "queued", "progress": 0, "kind": "video", "prompt": prompt,
            "model": model, "source": source, "extensions": new_meta["extensions"]}


def _find_video(interaction: dict[str, Any]) -> dict[str, Any] | None:
    for step in interaction.get("steps") or []:
        for item in step.get("content") or []:
            if item.get("type") == "video":
                return item
    return None


def _find_text(interaction: dict[str, Any]) -> str:
    texts = [
        item.get("text", "")
        for step in interaction.get("steps") or []
        for item in step.get("content") or []
        if item.get("type") == "text"
    ]
    return " ".join(t for t in texts if t)[:300]


async def _run(job: str, body: dict[str, Any]) -> None:
    entry = _TASKS[job]
    try:
        key = veo._key()
        headers = {"x-goog-api-key": key}
        deadline = time.time() + RENDER_TIMEOUT
        timeout = httpx.Timeout(RENDER_TIMEOUT, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http:
            r = await http.post(f"{veo.API}/interactions", headers=headers, json=body)
            if r.status_code != 200:
                raise veo.VeoError(r.status_code, veo._google_error(r))
            data = r.json()

            # If the call came back before the render finished, poll it.
            while data.get("status") and data["status"] not in _TERMINAL:
                if time.time() > deadline:
                    raise veo.VeoError(504, "Omni Flash took too long to render.")
                await asyncio.sleep(5)
                g = await http.get(f"{veo.API}/interactions/{data['id']}", headers=headers)
                if g.status_code != 200:
                    raise veo.VeoError(g.status_code, veo._google_error(g))
                data = g.json()

            if data.get("status") not in (None, "completed"):
                detail = (data.get("error") or {}).get("message") or _find_text(data)
                raise veo.VeoError(502, f"Omni Flash ended with status '{data.get('status')}'"
                                        + (f": {detail}" if detail else ""))

            video = _find_video(data)
            if not video:
                said = _find_text(data)
                raise veo.VeoError(502, "Omni Flash returned no video"
                                        + (f" - it said: {said}" if said else
                                           ". The prompt may have been blocked; rephrase it."))

            if video.get("data"):
                raw = base64.b64decode(video["data"])
                uri = None
            else:
                uri = video["uri"]
                # A uri-delivered file is PROCESSING until it turns ACTIVE.
                match = re.search(r"files/([^:/?]+)", uri)
                if match:
                    while True:
                        f = await http.get(f"{veo.API}/files/{match.group(1)}", headers=headers)
                        state = f.json().get("state") if f.status_code == 200 else None
                        if state == "ACTIVE" or f.status_code != 200:
                            break
                        if state == "FAILED":
                            raise veo.VeoError(502, "Google failed to process the rendered video.")
                        if time.time() > deadline:
                            raise veo.VeoError(504, "The rendered video never became ready.")
                        await asyncio.sleep(4)
                download = await http.get(uri, headers=headers)
                if download.status_code != 200:
                    raise veo.VeoError(download.status_code, "Could not download the finished video")
                raw = download.content

        meta = {**entry["meta"], "interaction": data.get("id"), "uri": uri}
        name, seconds = veo.save_video(raw, meta, tail=job[-10:])
        entry.update({"status": "completed", "file": name, "seconds": seconds})
    except veo.VeoError as error:
        entry.update({"status": "failed", "error": f"Omni Flash error ({error.status}): {error.message}"})
    except Exception as error:  # noqa: BLE001 - surfaced to the page via the poll
        entry.update({"status": "failed", "error": f"{type(error).__name__}: {error}"})
    finally:
        entry.pop("task", None)


async def status(job: str) -> dict[str, Any]:
    entry = _TASKS.get(job)
    if entry is None:
        raise veo.VeoError(404, "Unknown Omni job - the server may have restarted. "
                                "Anything that finished is in the gallery.")
    payload: dict[str, Any] = {"id": job, "status": entry["status"], "progress": 0}
    if entry["status"] == "failed":
        payload["error"] = entry.get("error")
    if entry["status"] == "completed":
        name = entry["file"]
        payload.update({"progress": 100, "file": name, "url": f"/api/creative/asset/{name}",
                        "seconds": entry.get("seconds")})
    return payload
