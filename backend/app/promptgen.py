"""
Creative idea generator: brand + account performance + live trend research.

The point of difference from "ask a model for ad ideas" is what goes in:

- the **brand persona** from `brand/persona.md`, so ideas sound like ULTEx
- the account's **actual top performers** -- the angles already proven to
  convert here, rather than generic best practice
- **live web search**, localised to Morocco, so "tendance" means something
  current instead of whatever the model remembers

Each idea comes back with a ready-to-paste image prompt and a video prompt, so
the output drops straight into the creative studio rather than needing a second
round of writing.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import openai

from .config import get_settings
from .creative import load_persona

SYSTEM = """You are a creative director for a Moroccan import-export and \
logistics company, briefing a team that will generate the assets with an image \
or video model.

Ground every idea in three things, in this order of authority:
1. The brand persona you are given - voice, visual direction, what to avoid.
2. What is already working in this ad account. An angle that converts here \
beats a clever one that does not.
3. Current context from your web search - season, trade events, shipping \
conditions, local moments. Use it only where it genuinely fits the business; a \
forced trend tie-in is worse than none.

For each idea produce:
- a short title
- the angle in one sentence: what it says to a Moroccan importer and why now
- `image_prompt`: a complete, self-contained prompt for an image model. \
Describe subject, setting, lighting, composition and mood. Leave one corner \
uncluttered for a logo. Never ask for text, logos or watermarks to be drawn.
- `video_prompt`: the same idea as a 4-8 second clip. Describe the motion and \
what the camera does.
- `why`: which account data or trend this is based on. Name the number or the \
source. If it is based on nothing but judgement, say so.

Rules. Write prompts in English (image models follow it more reliably) but any \
on-screen copy suggestions in French. Never invent account figures - use only \
what you were given. Prefer real, shootable scenes over abstract concepts. Do \
not propose anything the persona's "avoid" list rules out."""


class PromptGenNotConfigured(RuntimeError):
    pass


def _client() -> openai.AsyncOpenAI:
    key = get_settings().openai_api_key
    try:
        return openai.AsyncOpenAI(api_key=key) if key else openai.AsyncOpenAI()
    except Exception as cause:  # noqa: BLE001
        raise PromptGenNotConfigured(
            "No OpenAI credentials found. Set OPENAI_API_KEY in .env.local and restart."
        ) from cause


def account_evidence(snapshot: dict, audit: dict, limit: int = 6) -> dict[str, Any]:
    """The performance facts an idea should be built on.

    Only entities with enough conversions to mean anything are offered as
    "what works" -- otherwise the generator builds a campaign concept on a
    two-conversion fluke, which is the same small-sample trap the strategist
    had.
    """
    currency = snapshot.get("currency", "USD")

    converting = [
        c for c in snapshot["campaigns"]
        if c["metrics"].get("results", 0) >= 10 and c["metrics"].get("spend", 0) > 0
    ]
    converting.sort(key=lambda c: c["metrics"].get("costPerResult", 0))

    ads = [
        a for a in snapshot["ads"]
        if a["metrics"].get("impressions", 0) >= 500
    ]
    ads.sort(key=lambda a: -(a["metrics"].get("results", 0)))

    return {
        "currency": currency,
        "window": snapshot.get("windowLabel"),
        "account_metrics": {
            "spend": round(snapshot["accountMetrics"].get("spend", 0), 2),
            "results": snapshot["accountMetrics"].get("results", 0),
            "cost_per_result": round(snapshot["accountMetrics"].get("costPerResult", 0), 2),
            "ctr": round(snapshot["accountMetrics"].get("ctr", 0), 2),
        },
        "best_campaigns": [
            {
                "name": c.get("name"),
                "objective": c.get("objective"),
                "cost_per_result": round(c["metrics"]["costPerResult"], 2),
                "results": c["metrics"]["results"],
            }
            for c in converting[:limit]
        ],
        "top_ads": [
            {
                "name": a.get("name"),
                "creative_type": (a.get("creative") or {}).get("object_type"),
                "results": a["metrics"].get("results", 0),
                "ctr": round(a["metrics"].get("ctr", 0), 2),
                "quality_ranking": (a.get("insights") or {}).get("quality_ranking"),
            }
            for a in ads[:limit]
        ],
        "note": (
            "Campaigns listed have at least 10 conversions; ads have at least 500 "
            "impressions. Anything below that was withheld as too small to learn from."
        ),
    }


IDEAS_SCHEMA = {
    "type": "object",
    "properties": {
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "angle": {"type": "string"},
                    "image_prompt": {"type": "string"},
                    "video_prompt": {"type": "string"},
                    "why": {"type": "string"},
                    "french_copy": {
                        "type": "string",
                        "description": "Suggested on-screen or caption copy, in French.",
                    },
                },
                "required": ["title", "angle", "image_prompt", "video_prompt", "why", "french_copy"],
                "additionalProperties": False,
            },
        },
        "trends_used": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What the web search actually turned up and informed these ideas.",
        },
    },
    "required": ["ideas", "trends_used"],
    "additionalProperties": False,
}


async def generate_ideas(
    *,
    snapshot: dict | None,
    audit: dict | None,
    brief: str = "",
    count: int = 4,
    use_trends: bool = True,
) -> dict[str, Any]:
    """Produce creative briefs with ready-to-use prompts."""
    client = _client()
    settings = get_settings()
    persona = load_persona()

    parts = [f"Today is {date.today().isoformat()}."]
    if persona:
        parts.append(f"BRAND PERSONA\n{persona}")
    if snapshot and audit:
        parts.append(
            "WHAT IS WORKING IN THIS ACCOUNT\n"
            + json.dumps(account_evidence(snapshot, audit), indent=2)
        )
    if brief.strip():
        parts.append(f"SPECIFIC BRIEF FROM THE TEAM\n{brief.strip()}")
    if use_trends:
        parts.append(
            "Search the web for what is currently relevant to importers and "
            "logistics in Morocco - freight rates, port and customs news, trade "
            "events, seasonal demand, anything timely. Then use what you find."
        )
    parts.append(f"Produce {count} distinct ideas. Make them genuinely different from each other.")

    tools = []
    if use_trends:
        tools.append({
            "type": "web_search",
            "search_context_size": "medium",
            # Localised: freight and customs context for Morocco, not the US.
            "user_location": {"type": "approximate", "country": "MA", "city": "Casablanca"},
        })

    response = await client.responses.create(
        model=settings.openai_model,
        instructions=SYSTEM,
        input="\n\n".join(parts),
        tools=tools or openai.NOT_GIVEN,
        text={
            "format": {
                "type": "json_schema",
                "name": "creative_ideas",
                "schema": IDEAS_SCHEMA,
                "strict": True,
            }
        },
    )

    raw = response.output_text
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as cause:
        raise RuntimeError(f"Idea generator returned unparseable JSON: {cause}") from cause

    searched = any(
        getattr(item, "type", "") == "web_search_call" for item in (response.output or [])
    )
    return {
        **payload,
        "personaApplied": bool(persona),
        "accountDataUsed": bool(snapshot and audit),
        "webSearchUsed": searched,
        "model": settings.openai_model,
    }
