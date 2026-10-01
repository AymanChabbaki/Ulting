"""
Creative idea generator.

The first version produced generic logistics clichés, and the reason was the
input, not the prompt: it told the model the account's winners were "Image 2"
and "AD4" -- names, with no idea what those ads said or showed. So the model
had nothing to learn from and fell back on stock imagery of container ships.

This version feeds it what a human creative strategist would actually look at:

- **the real ads**, fetched from Meta: headline, body copy, CTA and the image
  itself (sent to the model as an image), for the best performers *and* the
  ones that spent without converting -- with their numbers
- **the brand persona** from `brand/persona.md`
- **a sourcing calendar** for Moroccan importers -- Golden Week, Canton Fair,
  Black Friday stock-up, Chinese New Year order cut-offs, Ramadan, the Loi de
  Finances -- because for an importer the decision happens weeks before the
  event, which is exactly the angle a generic trend search misses
- **live web search**, localised to Morocco, for what is happening now

and asks a reasoning model to brainstorm wide, discard the generic, and return
complete ads: hook, French copy, headline, CTA, an image prompt built to a
fixed formula, and a shot-by-shot video prompt.
"""

from __future__ import annotations

import base64
import json
from datetime import date, timedelta
from typing import Any

import httpx
import openai

from .config import get_settings
from .creative import _reference_bytes, load_persona, style_references
from .meta.client import MetaApiError, graph_get_all
from .meta.collect import normalise_account_id

DEFAULT_CREATIVE_MODEL = "gpt-5.5"

SYSTEM = """You are a senior performance creative strategist. You write Meta \
(Facebook and Instagram) ads for ULTEx, a Moroccan import-export and logistics \
company. Your ideas go straight to production: an image model and a video model \
will render your prompts, and the copy will be published as written.

## How you work

1. Read the evidence first. You are given the account's real ads - their copy, \
their images and their numbers. Work out *why* the winners won (the angle, the \
promise, the visual) and why the losers lost. That is your strongest signal. \
If a winning ad's copy is off-brand or off-topic for ULTEx, take the angle that \
converted, not its content, and say so.
2. Pick a sharp insight for each idea - one specific fear, frustration or desire \
of a Moroccan importer. Treat the pain points below as hypotheses; prefer what \
the evidence and the brief tell you.
3. Brainstorm at least three times as many concepts as you are asked for, \
across different frameworks. Then kill the generic ones. The test: if you could \
swap ULTEx for any freight forwarder and the ad still works, it is too generic - \
make it specific to ULTEx's promise (one partner for the whole chain, factory \
to door, offices in China, Turkey, Europe, Africa, India, Moroccan customs \
expertise).
4. Return only the best ideas. Each one must use a different framework AND a \
different insight. No two ideas may share a hook structure.

## Audience pain points (hypotheses)

- Container blocked at customs in Casablanca or Tanger Med, with surprise fees \
and storage charges piling up
- Not knowing where the goods are, chasing five different providers on WhatsApp
- Supplier risk abroad: wrong quality, missing goods, deposits lost, language \
barriers, no one on the ground to check
- Landed cost they cannot predict: freight, duties, fees, exchange rate
- Too small for a full container; groupage and consolidation feel risky
- Paperwork and compliance: certificates, conformity, import licences
- Getting goods from the port to an inland city or warehouse on time
- Seasonal pressure: stock must land before Ramadan, the rentrée, Black Friday

## Frameworks (use a different one per idea)

pain_agitate_solve, before_after, proof_numbers, behind_the_scenes, \
objection_handling, process_steps, comparison, seasonal_urgency, testimonial_ugc, \
offer_lead_magnet

## Meta craft

- The first line of copy and the first second of video must stop the scroll on \
their own. Lead with the importer's problem or a concrete outcome, never the \
company name.
- One idea, one message. Specific beats clever: named ports, named cities, real \
product categories (tyres, appliances, electronics, agricultural machinery).
- Static ads are designed posters in the ULTEx house style (see the reference \
ads attached and the persona): one strong visual metaphor, a two-beat headline, \
a subline, a CTA. The metaphor carries the idea - it must be new, not one of \
the references' (tangled wires, dominoes, messy desk, twelve contacts). Video \
can be native and documentary: real operations, real people, real places.
- Proof only from what you are given (the persona's figures, the account \
evidence). Never invent statistics, prices, transit times or testimonials. A \
testimonial idea must be written as a script for a real client to record, not \
as a fake quote.

## Copy

- Language as requested (default French; Moroccan business register, \
vouvoiement). Darija when asked: written in Latin script as Moroccans text it.
- `hook`: the first line, under ~90 characters.
- `primary_text`: hook + 2-4 short lines + a clear next step. Short sentences.
- `headline`: under ~40 characters.
- `cta`: one Meta button, e.g. LEARN_MORE, GET_QUOTE, CONTACT_US, \
WHATSAPP_MESSAGE, SIGN_UP, GET_OFFER.

## On-image text (French, exact - it is rendered in the image)

- `image_headline`: two short beats separated by " / " - a setup and a punchline \
("Une petite erreur. / Un grand effet."). Max ~45 characters in total.
- `image_subline`: one short sentence on what ULTEx does about it (max ~70 chars).
- `image_cta`: the button text, 2-4 words ("Centralisez votre importation").
- `image_benefits`: 0, 3 or 4 short uppercase labels for the navy benefit band \
(e.g. MOINS DE RISQUES, GAIN DE TEMPS); empty when the ad is cleaner without.
These follow the copy language, except that Darija on-image text stays in Latin \
script and short.

## image_prompt (English, 80-140 words) - the brief for the image designer

It describes ONE finished designed ad; the studio sends the logo and the house \
reference ads with it, so do not describe the logo itself. In order:
1. The visual metaphor - the hero object(s), exactly what they are, how they are \
arranged and what each one means; French labels on objects if they help \
(Fournisseur, Douane, Documents, Délais...).
2. Style - premium studio 3D still-life or realistic photography, soft studio \
light, light off-white / pale grey / white marble background, navy and white \
with brand blue #0b5cb8 and sparing yellow #f8c000.
3. Layout - logo top-left; where the hero sits; headline, yellow rule, subline \
and CTA positions; the benefit band if `image_benefits` is not empty.
4. The exact text, quoted: Headline: "...", Subline: "...", CTA: "...", and the \
benefit labels if any - identical to the image_* fields.
5. Exclusions - no other text, no other logos, no watermarks, no stock handshakes.

## video_prompt (English, vertical 9:16, 8 seconds, three beats)

"0-2s: ... | 2-5s: ... | 5-8s: ..." - say what is on screen, what moves and what \
the camera does in each beat. The 0-2s beat is the hook and must work with the \
sound off. No on-screen text, no logos.

## Accountability

For every idea, `why` names the evidence it builds on (which ad, which number, \
which calendar moment or search result) or says plainly that it is a judgement \
call. `test_hypothesis` states what the idea will teach us if we run it."""


IDEA_SCHEMA = {
    "type": "object",
    "properties": {
        "strategy_summary": {
            "type": "string",
            "description": "2-4 sentences: what the evidence says is working, what is not, "
                           "and the direction these ideas take.",
        },
        "evidence_notes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Anything in the account the team should know - e.g. off-brand "
                           "copy on a winning ad, too little data to learn from.",
        },
        "trends_used": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Calendar moments and search findings that informed the ideas.",
        },
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "framework": {
                        "type": "string",
                        "enum": [
                            "pain_agitate_solve", "before_after", "proof_numbers",
                            "behind_the_scenes", "objection_handling", "process_steps",
                            "comparison", "seasonal_urgency", "testimonial_ugc",
                            "offer_lead_magnet",
                        ],
                    },
                    "audience": {"type": "string"},
                    "insight": {"type": "string"},
                    "hook": {"type": "string"},
                    "primary_text": {"type": "string"},
                    "headline": {"type": "string"},
                    "cta": {"type": "string"},
                    "placement": {
                        "type": "string",
                        "enum": ["feed_square", "feed_portrait", "story_reel_vertical"],
                    },
                    "image_headline": {"type": "string"},
                    "image_subline": {"type": "string"},
                    "image_cta": {"type": "string"},
                    "image_benefits": {"type": "array", "items": {"type": "string"}},
                    "image_prompt": {"type": "string"},
                    "video_prompt": {"type": "string"},
                    "why": {"type": "string"},
                    "test_hypothesis": {"type": "string"},
                },
                "required": [
                    "title", "framework", "audience", "insight", "hook", "primary_text",
                    "headline", "cta", "placement", "image_headline", "image_subline",
                    "image_cta", "image_benefits", "image_prompt", "video_prompt", "why",
                    "test_hypothesis",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["strategy_summary", "evidence_notes", "trends_used", "ideas"],
    "additionalProperties": False,
}


class PromptGenNotConfigured(RuntimeError):
    pass


def _client() -> openai.AsyncOpenAI:
    key = get_settings().openai_api_key
    try:
        # Reasoning at high effort plus web search can take a couple of
        # minutes; the default would cut it off. Both proxies allow 300s.
        kwargs = {"timeout": 280.0, "max_retries": 1}
        return openai.AsyncOpenAI(api_key=key, **kwargs) if key else openai.AsyncOpenAI(**kwargs)
    except Exception as cause:  # noqa: BLE001
        raise PromptGenNotConfigured(
            "No OpenAI credentials found. Set OPENAI_API_KEY in .env.local and restart."
        ) from cause


# ------------------------------------------------------------------ calendar

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


# Lunar-calendar dates are approximate (they depend on moon sighting) and are
# flagged as such -- the model is told to confirm them with search.
_CHINESE_NEW_YEAR = {2026: date(2026, 2, 17), 2027: date(2027, 2, 6), 2028: date(2028, 1, 26)}
_RAMADAN_START = {2026: date(2026, 2, 18), 2027: date(2027, 2, 8), 2028: date(2028, 1, 28)}
_EID_AL_ADHA = {2026: date(2026, 5, 27), 2027: date(2027, 5, 16), 2028: date(2028, 5, 5)}


# 150 days, not 120: Chinese New Year and Ramadan sit ~4 months out for much
# of the year, but their order cut-offs are weeks earlier -- a shorter window
# drops exactly the deadlines an importer most needs reminding of.
def sourcing_calendar(today: date | None = None, horizon_days: int = 150) -> list[dict[str, Any]]:
    """Moments that matter to a Moroccan importer, soonest first.

    The point is lead time: goods shipped by sea from Asia take weeks, so the
    buying decision happens well before the event. An ad that talks about
    Ramadan stock in Ramadan is too late; one that runs while orders can still
    land in time is the useful one.
    """
    today = today or date.today()
    moments: list[tuple[date, str, str, bool]] = []
    for year in (today.year, today.year + 1):
        moments += [
            (date(year, 10, 1), "China Golden Week (1-7 Oct)",
             "Chinese factories and ports slow down for a week; orders placed just "
             "before it slip. Production and shipping backlogs follow.", False),
            (date(year, 10, 15), "Canton Fair, autumn session (mid-October)",
             "Peak sourcing season in Guangzhou; importers meet and vet new suppliers.", False),
            (date(year, 4, 15), "Canton Fair, spring session (mid-April)",
             "Peak sourcing season in Guangzhou; importers meet and vet new suppliers.", False),
            (date(year, 11, 11), "11.11 / Singles' Day",
             "E-commerce sellers stock up; goods must already be in Morocco.", False),
            (_nth_weekday(year, 11, 4, 4), "Black Friday",
             "Retail and e-commerce stock must land in Morocco weeks before.", False),
            (date(year, 12, 31), "Loi de Finances takes effect (1 January)",
             "Customs duties and taxes can change with the new finance law; importers "
             "want to know their landed cost before and after.", False),
            (date(year, 9, 5), "Rentrée scolaire",
             "School supplies, electronics and furniture demand; stock lands in summer.", False),
            (date(year, 7, 1), "Summer / MRE season",
             "Diaspora returns; consumer demand and port congestion rise.", False),
        ]
        if year in _CHINESE_NEW_YEAR:
            cny = _CHINESE_NEW_YEAR[year]
            moments.append((cny, "Chinese New Year (approx.)",
                            "Chinese factories close for weeks around it. The real deadline "
                            "is the last order that ships before the shutdown, well ahead "
                            "of the date.", True))
        if year in _RAMADAN_START:
            moments.append((_RAMADAN_START[year], "Ramadan begins (approx.)",
                            "Food, household and consumer-goods demand peaks; imported "
                            "stock must already be on shelves.", True))
        if year in _EID_AL_ADHA:
            moments.append((_EID_AL_ADHA[year], "Eid al-Adha (approx.)",
                            "Household spending peak; appliances and consumer goods.", True))

    out = []
    for when, name, why, approx in sorted(moments):
        days = (when - today).days
        if -7 <= days <= horizon_days:
            out.append({
                "moment": name,
                "date": when.isoformat(),
                "days_away": days,
                "approximate_date": approx,
                "relevance": why,
            })
    return out


# ------------------------------------------------------------------ evidence

def _text_of(creative: dict) -> tuple[str, str, str | None, str | None]:
    """Headline, body, CTA and image URL, wherever Meta put them."""
    oss = creative.get("object_story_spec") or {}
    link = oss.get("link_data") or {}
    video = oss.get("video_data") or {}
    afs = creative.get("asset_feed_spec") or {}
    title = creative.get("title") or link.get("name") or video.get("title") or ""
    body = creative.get("body") or link.get("message") or video.get("message") or ""
    if not body and afs.get("bodies"):
        body = afs["bodies"][0].get("text", "")
    if not title and afs.get("titles"):
        title = afs["titles"][0].get("text", "")
    cta = creative.get("call_to_action_type") or (
        (link.get("call_to_action") or video.get("call_to_action") or {}).get("type")
    )
    image = creative.get("image_url") or link.get("picture") or video.get("image_url") \
        or creative.get("thumbnail_url")
    return title.strip(), body.strip(), cta, image


async def creative_evidence(account_id: str, snapshot: dict) -> dict[str, Any]:
    """The account's real ads, grouped by message, with their results.

    Ads that share the same copy are merged: Meta accounts are full of the same
    creative duplicated across ad sets, and five copies of one message would
    otherwise crowd out everything else the model could learn from.
    """
    metrics = {a["id"]: a["metrics"] for a in snapshot.get("ads", [])}
    try:
        rows = await graph_get_all(
            f"{normalise_account_id(account_id)}/ads",
            {
                "fields": "id,name,status,creative{title,body,call_to_action_type,image_url,"
                          "thumbnail_url,object_type,object_story_spec,asset_feed_spec}",
                "limit": 100,
            },
        )
    except MetaApiError:
        return {"winners": [], "losers": [], "note": "Could not read ad creatives from Meta."}

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for ad in rows:
        m = metrics.get(ad["id"])
        if not m:
            continue
        title, body, cta, image = _text_of(ad.get("creative") or {})
        if not (title or body):
            continue
        key = (title[:200], body[:400])
        g = groups.setdefault(key, {
            "headline": title, "body": body[:600], "cta": cta,
            "format": (ad.get("creative") or {}).get("object_type"),
            "ad_names": [], "image_url": image,
            "spend": 0.0, "results": 0.0, "impressions": 0.0, "clicks": 0.0,
        })
        g["ad_names"].append(ad.get("name"))
        g["image_url"] = g["image_url"] or image
        for k in ("spend", "results", "impressions", "clicks"):
            g[k] += m.get(k, 0) or 0

    creatives = []
    for g in groups.values():
        g["cost_per_result"] = round(g["spend"] / g["results"], 2) if g["results"] else None
        g["ctr"] = round(g["clicks"] / g["impressions"] * 100, 2) if g["impressions"] else 0
        g["spend"] = round(g["spend"], 2)
        g["reliability"] = (
            "solid" if g["results"] >= 30 else
            "directional" if g["results"] >= 5 else
            "too little data"
        )
        g["ad_names"] = sorted(set(n for n in g["ad_names"] if n))[:5]
        creatives.append(g)

    winners = sorted(
        (c for c in creatives if c["results"] >= 3),
        key=lambda c: (c["cost_per_result"] or 1e9),
    )[:4]
    # Spent real money without converting -- what not to repeat.
    losers = sorted(
        (c for c in creatives if c["spend"] >= 10 and c["results"] == 0),
        key=lambda c: -c["spend"],
    )[:3]
    return {"winners": winners, "losers": losers, "currency": snapshot.get("currency", "USD")}


async def _image_inputs(urls: list[str], limit: int = 3) -> list[dict[str, Any]]:
    """Download winning creatives and attach them so the model can see them.

    Downloaded server-side and sent as data URLs: Meta's CDN links are signed
    and short-lived, and OpenAI fetching them directly fails often enough to
    matter. Anything that fails or is too large is skipped, not fatal.
    """
    inputs = []
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as http:
        for url in urls[:limit]:
            try:
                r = await http.get(url)
                if r.status_code != 200 or len(r.content) > 4_000_000:
                    continue
                mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
                if not mime.startswith("image/"):
                    continue
                data = base64.b64encode(r.content).decode()
                inputs.append({
                    "type": "input_image",
                    "image_url": f"data:{mime};base64,{data}",
                    "detail": "auto",
                })
            except httpx.HTTPError:
                continue
    return inputs


def _reference_inputs() -> list[dict[str, Any]]:
    """The house-style reference ads, so ideas are written for that design."""
    inputs = []
    for path in style_references():
        _, data, mime = _reference_bytes(path)
        inputs.append({
            "type": "input_image",
            "image_url": f"data:{mime};base64,{base64.b64encode(data).decode()}",
            "detail": "low",
        })
    return inputs


# ------------------------------------------------------------------ generation

async def generate_ideas(
    *,
    snapshot: dict | None,
    audit: dict | None,
    account_id: str | None = None,
    brief: str = "",
    count: int = 4,
    use_trends: bool = True,
    audience: str = "",
    service: str = "",
    language: str = "fr",
    placement: str = "",
) -> dict[str, Any]:
    """Complete ad concepts, grounded in this account's real ads."""
    client = _client()
    settings = get_settings()
    model = settings.openai_creative_model or DEFAULT_CREATIVE_MODEL
    persona = load_persona()

    evidence = {"winners": [], "losers": []}
    if snapshot and account_id:
        evidence = await creative_evidence(account_id, snapshot)

    images = await _image_inputs([c["image_url"] for c in evidence["winners"] if c.get("image_url")])
    # Image payloads are sent separately; keep the JSON readable.
    evidence_for_text = {
        **evidence,
        "winners": [{k: v for k, v in c.items() if k != "image_url"} for c in evidence["winners"]],
        "losers": [{k: v for k, v in c.items() if k != "image_url"} for c in evidence["losers"]],
    }

    language_label = {
        "fr": "French",
        "darija": "Moroccan Darija in Latin script",
        "ar": "Modern Standard Arabic",
        "fr+darija": "French, with a Darija variant of the hook in `why`",
    }.get(language, "French")

    parts = [f"Today is {date.today().isoformat()}."]
    if persona:
        parts.append(f"# BRAND PERSONA\n{persona}")
    if snapshot:
        m = snapshot.get("accountMetrics", {})
        parts.append(
            "# ACCOUNT CONTEXT\n"
            f"Window: {snapshot.get('windowLabel')}. Currency {snapshot.get('currency')}. "
            f"Spend {m.get('spend', 0):.2f}, {int(m.get('results', 0))} {m.get('resultLabel', 'results')}, "
            f"cost per result {m.get('costPerResult', 0):.2f}, CTR {m.get('ctr', 0):.2f}%."
        )
    parts.append(
        "# THE ACCOUNT'S REAL ADS\n"
        "Winners are sorted by cost per result. Losers spent without converting. "
        f"{'The images of the winners are attached, in the same order.' if images else 'No creative images could be attached.'}\n"
        + json.dumps(evidence_for_text, indent=1, ensure_ascii=False)
    )
    parts.append(
        "# SOURCING CALENDAR (next ~4 months)\n"
        "Dates marked approximate follow the lunar calendar - confirm with search.\n"
        + json.dumps(sourcing_calendar(), indent=1, ensure_ascii=False)
    )

    steer = []
    if audience:
        steer.append(f"Audience: {audience}")
    if service:
        steer.append(f"Service to push: {service}")
    if placement:
        steer.append(f"Placement: {placement}")
    if brief.strip():
        steer.append(f"Brief from the team: {brief.strip()}")
    if steer:
        parts.append("# THIS REQUEST\n" + "\n".join(steer))

    if use_trends:
        parts.append(
            "# RESEARCH\nSearch the web for what is happening right now that matters to "
            "Moroccan importers: freight rates and shipping disruptions on Asia-Morocco "
            "routes, port and customs news for Casablanca and Tanger Med, the current "
            "finance law debate on import duties, trade fairs, and any timely consumer "
            "demand. Use only what genuinely fits."
        )

    parts.append(
        f"Write every piece of copy, including the on-image text, in {language_label}. "
        "Prompts stay in English, with the on-image text quoted exactly. "
        f"Return exactly {count} ideas, each with a different framework and insight."
    )

    references = _reference_inputs()
    if references:
        parts.append(
            f"# HOUSE STYLE\nThe last {len(references)} attached images are previous "
            "ULTEx ads. Every static idea must fit this design system - learn the "
            "layout and tone, never reuse their metaphors or wording."
        )

    content = [{"type": "input_text", "text": "\n\n".join(parts)}, *images, *references]
    tools = [{
        "type": "web_search",
        "search_context_size": "high",
        "user_location": {"type": "approximate", "country": "MA", "city": "Casablanca"},
    }] if use_trends else []

    request = dict(
        model=model,
        instructions=SYSTEM,
        input=[{"role": "user", "content": content}],
        reasoning={"effort": "high"},
        text={"format": {
            "type": "json_schema", "name": "creative_ideas",
            "schema": IDEA_SCHEMA, "strict": True,
        }},
    )
    if tools:
        request["tools"] = tools

    try:
        response = await client.responses.create(**request)
    except openai.BadRequestError as error:
        # Older or non-reasoning models reject the reasoning block; retry once
        # without it rather than failing the whole request.
        if "reasoning" not in str(error).lower():
            raise
        request.pop("reasoning")
        response = await client.responses.create(**request)

    try:
        payload = json.loads(response.output_text)
    except json.JSONDecodeError as cause:
        raise RuntimeError(f"Idea generator returned unparseable JSON: {cause}") from cause

    searched = any(getattr(i, "type", "") == "web_search_call" for i in (response.output or []))
    return {
        **payload,
        "model": model,
        "personaApplied": bool(persona),
        "webSearchUsed": searched,
        "imagesSeen": len(images),
        "learnedFrom": [
            {
                "headline": c["headline"][:80],
                "results": c["results"],
                "costPerResult": c["cost_per_result"],
                "reliability": c["reliability"],
            }
            for c in evidence["winners"]
        ],
        "avoided": [{"headline": c["headline"][:80], "spend": c["spend"]} for c in evidence["losers"]],
    }
