"""
The strategist: a marketing engineer that reads the account and proposes work.

Runs on OpenAI chat completions with function calling. Two design choices
drive everything here.

**Tools read the snapshot already in memory, not Meta.** The account tree,
insights and audit are collected once per (account, window) and cached; the
tools below slice that. So a ten-turn conversation costs zero Graph calls,
which matters on a development-access token where a burst of ~10 insights
calls is enough to trip the user-level limit. Only `get_breakdown` can reach
Meta, and it goes through the same 5-minute cache.

**The model is given data access rather than a data dump.** An account here is
10 campaigns / 21 ad sets / 82 ads; pasting all of it into every turn would
burn context on ads nobody asked about and still not cover the breakdowns. It
pulls what a question needs instead.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import openai

from .audit.rules import SEVERITY_ORDER
from .config import get_settings

SYSTEM = """You are the in-house performance marketing engineer for this Meta \
ads account. You have read access to its campaigns, ad sets, ads, audience and \
placement breakdowns, and a rule-based audit. Your job is to find what is \
costing money and say precisely what to change.

HOW YOU WORK

Pull data before answering. You have tools; use them. A question about \
performance gets numbers from a tool call, never a guess. If you have not \
looked, say so and look.

Never invent a number. Every figure you state must have come from a tool \
result in this conversation. If a tool returns nothing for something the user \
asked about, say the data is not there rather than estimating around it.

Lead with the recommendation, then the evidence. "Move the Audience Network \
budget into Reels" then the CPL figures that justify it - not a tour of the \
data ending in a conclusion.

Be specific enough to act on. Name the campaign, ad set or ad. Give the \
direction and rough size of the change. "Cut the daily budget on X from $25 to \
$15 and move it to Y" beats "consider reallocating budget".

Quantify when the data supports it, and only then. If 3 placements are at \
$3.84 CPL and one is at $37.13, the arithmetic on moving that spend is worth \
showing. If a campaign has 1 conversion, say the sample is too small to \
conclude anything - do not rank it.

JUDGEMENT YOU ARE EXPECTED TO APPLY

- Small samples lie. Under ~30 conversions, differences are usually noise. Say \
that instead of ranking noise.
- The learning phase matters. An ad set with fewer than ~50 optimisation \
events a week is not being optimised, and its cost per result is not a fair \
read on the creative or the audience.
- Editing an ad set restarts learning. Warn when your own advice would.
- Attribution windows are not comparable across ad sets. If they differ, cost \
per result is not comparable either.
- Currency never crosses accounts. Never add figures from accounts on \
different currencies.
- Frequency above ~3 means fatigue, not a bad audience.
- Correlation in a breakdown is not causation: a placement can look cheap \
because the campaigns that ran there had better creative.

WHAT YOU DO NOT DO

You have read access only - you cannot change the account. Give the user the \
change to make; do not imply you have made it.

Do not pad. No preamble, no summary of what you are about to do, no closing \
offer of further help. If the answer is two sentences, write two sentences.

Do not hedge everything into uselessness. When the data supports a clear call, \
make it."""


# ---------------------------------------------------------------- tool schemas

def _fn(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    """One function tool in the shape chat completions expects."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


TOOLS: list[dict[str, Any]] = [
    _fn(
        "get_account_overview",
        "Account-level totals for the reporting window: spend, results, cost per result, "
        "CTR, CPM, frequency, the day-by-day trend, and the same metrics for the previous "
        "equal-length period. Start here when the question is broad ('how are we doing', "
        "'what should I fix').",
        {},
    ),
    _fn(
        "list_campaigns",
        "Campaigns with their metrics. Use to compare campaigns, find the expensive ones, "
        "or see what is live.",
        {
            "status": {"type": "string", "enum": ["ACTIVE", "PAUSED", "ANY"],
                       "description": "Defaults to ANY."},
            "min_spend": {"type": "number",
                          "description": "Only campaigns that spent at least this much."},
            "sort_by": {"type": "string",
                        "enum": ["spend", "results", "cost_per_result", "ctr"],
                        "description": "Defaults to spend, descending."},
            "limit": {"type": "integer", "description": "Defaults to 25."},
        },
    ),
    _fn(
        "list_adsets",
        "Ad sets with metrics, budget, optimisation goal, learning stage and a targeting "
        "summary. Use when diagnosing delivery, audience overlap, budget split or "
        "learning-phase problems.",
        {
            "campaign_id": {"type": "string"},
            "status": {"type": "string", "enum": ["ACTIVE", "PAUSED", "ANY"]},
            "learning_limited_only": {"type": "boolean"},
            "limit": {"type": "integer", "description": "Defaults to 25."},
        },
    ),
    _fn(
        "list_ads",
        "Ads with metrics and Meta's quality / engagement / conversion rankings. Use for "
        "creative questions: which creative works, which is fatiguing, which was never "
        "given enough delivery to judge.",
        {
            "adset_id": {"type": "string"},
            "campaign_id": {"type": "string"},
            "min_impressions": {"type": "number",
                                "description": "Filter out starved ads. ~500 is where "
                                               "rankings appear."},
            "status": {"type": "string", "enum": ["ACTIVE", "PAUSED", "ANY"]},
            "limit": {"type": "integer", "description": "Defaults to 20."},
        },
    ),
    _fn(
        "get_breakdown",
        "Performance split by one dimension: placement, platform, device, age, gender, "
        "age_gender, country, region or hour. This is where misallocation usually shows "
        "up. May call Meta (cached 5 minutes).",
        {
            "cut": {"type": "string",
                    "enum": ["placement", "platform", "device", "age", "gender",
                             "age_gender", "country", "region", "hour"]},
        },
        required=["cut"],
    ),
    _fn(
        "get_audit_findings",
        "The rule-based audit: what is broken, how severe, the evidence it fired on, and "
        "the estimated recoverable spend. Use to ground a 'what should I fix' answer, or "
        "to check whether a problem you suspect is already known.",
        {
            "severity": {"type": "string",
                         "enum": ["critical", "high", "medium", "low", "info"]},
            "category": {"type": "string"},
            "limit": {"type": "integer", "description": "Defaults to 25."},
        },
    ),
]


# -------------------------------------------------------------- tool execution

# Metrics every row carries. A zero here is information ("this campaign got no
# leads"), so these are kept even when empty.
CORE_METRICS = (
    "spend", "impressions", "clicks", "results", "resultLabel",
    "costPerResult", "ctr", "cpm", "frequency",
)

# Kept only when non-zero. On a lead-gen account `revenue`, `roas` and
# `purchase`-derived fields are zero on every single row, and repeating them
# across 30 ads is pure context cost.
OPTIONAL_METRICS = (
    "reach", "linkClicks", "landingPageViews", "linkCtr",
    "cpc", "resultRate", "lpvRate", "revenue", "roas", "resultActionType",
)


def _round_metrics(m: dict[str, Any]) -> dict[str, Any]:
    """Project metrics down to what the model can use.

    Two trims. Floats are rounded -- raw insights carry values like
    6.921772151898734, and fifteen significant figures is noise on every row
    that also invites false precision in the answer. And optional metrics are
    dropped when zero: at ~780 chars per ad, 30 ads was 23K characters of tool
    result, much of it `"roas": 0.0` repeated.
    """
    m = m or {}
    out: dict[str, Any] = {}

    def put(key: str) -> None:
        value = m.get(key)
        if isinstance(value, float):
            out[key] = round(value, 4 if abs(value) < 10 else 2)
        elif value not in (None, ""):
            out[key] = value

    for key in CORE_METRICS:
        put(key)
    for key in OPTIONAL_METRICS:
        if m.get(key):
            put(key)
    return out


def _attribution_label(spec: Any) -> str | None:
    """Compact Meta's attribution_spec into something readable.

    The raw shape is [{"event_type": "CLICK_THROUGH", "window_days": 7}, ...],
    repeated on every ad set row. "7d click / 1d view" says the same thing in a
    tenth of the characters, and is what the model has to compare anyway.
    """
    if not spec:
        return None
    short = {"CLICK_THROUGH": "click", "VIEW_THROUGH": "view", "ENGAGED_VIDEO_VIEW": "video"}
    parts = [
        f"{e.get('window_days')}d {short.get(e.get('event_type'), e.get('event_type'))}"
        for e in spec
        if isinstance(e, dict)
    ]
    return " / ".join(parts) or None


def _targeting_summary(adset: dict) -> dict[str, Any]:
    t = adset.get("targeting") or {}
    geo = t.get("geo_locations") or {}
    return {
        "countries": geo.get("countries") or [],
        "cities": [c.get("name") for c in (geo.get("cities") or [])],
        "age": [t.get("age_min"), t.get("age_max")],
        "genders": t.get("genders") or [],
        "custom_audiences": len(t.get("custom_audiences") or []),
    }


def _sort_key(name: str):
    return {
        "spend": lambda e: -e["metrics"].get("spend", 0),
        "results": lambda e: -e["metrics"].get("results", 0),
        "cost_per_result": lambda e: -e["metrics"].get("costPerResult", 0),
        "ctr": lambda e: -e["metrics"].get("ctr", 0),
    }.get(name, lambda e: -e["metrics"].get("spend", 0))


def _match_status(entity: dict, wanted: str | None) -> bool:
    if not wanted or wanted == "ANY":
        return True
    return entity.get("status") == wanted


async def execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    snapshot: dict,
    audit: dict,
    breakdown_fetcher,
) -> Any:
    """Run one tool against the in-memory snapshot. Read-only by construction."""
    currency = snapshot.get("currency", "USD")

    if name == "get_account_overview":
        account = snapshot["account"]
        return {
            "account": account.get("name"),
            "currency": currency,
            "window": snapshot.get("windowLabel"),
            "metrics": _round_metrics(snapshot["accountMetrics"]),
            "previous_period": _round_metrics(snapshot.get("previousMetrics") or {}),
            "structure": {
                "campaigns": len(snapshot["campaigns"]),
                "active_campaigns": sum(1 for c in snapshot["campaigns"] if c.get("status") == "ACTIVE"),
                "adsets": len(snapshot["adsets"]),
                "ads": len(snapshot["ads"]),
            },
            "daily_trend": snapshot.get("trend", [])[-30:],
            "health_score": audit.get("score"),
            "estimated_recoverable_spend": audit.get("estimatedWaste"),
        }

    if name == "list_campaigns":
        rows = [c for c in snapshot["campaigns"] if _match_status(c, args.get("status"))]
        if args.get("min_spend") is not None:
            rows = [c for c in rows if c["metrics"].get("spend", 0) >= args["min_spend"]]
        rows.sort(key=_sort_key(args.get("sort_by", "spend")))
        return {
            "currency": currency,
            "count": len(rows),
            "campaigns": [
                {
                    "id": c["id"],
                    "name": c.get("name"),
                    "status": c.get("status"),
                    "effective_status": c.get("effective_status"),
                    "objective": c.get("objective"),
                    "daily_budget": float(c.get("daily_budget") or 0) / 100 or None,
                    "bid_strategy": c.get("bid_strategy"),
                    "metrics": _round_metrics(c["metrics"]),
                }
                for c in rows[: args.get("limit", 25)]
            ],
        }

    if name == "list_adsets":
        rows = [a for a in snapshot["adsets"] if _match_status(a, args.get("status"))]
        if args.get("campaign_id"):
            rows = [a for a in rows if a.get("campaign_id") == args["campaign_id"]]
        if args.get("learning_limited_only"):
            rows = [
                a for a in rows
                if (a.get("learning_stage_info") or {}).get("status") == "LEARNING_LIMITED"
            ]
        rows.sort(key=_sort_key("spend"))
        return {
            "currency": currency,
            "count": len(rows),
            "adsets": [
                {
                    "id": a["id"],
                    "name": a.get("name"),
                    "campaign_id": a.get("campaign_id"),
                    "status": a.get("status"),
                    "effective_status": a.get("effective_status"),
                    "daily_budget": float(a.get("daily_budget") or 0) / 100 or None,
                    "optimization_goal": a.get("optimization_goal"),
                    "bid_strategy": a.get("bid_strategy"),
                    "learning_stage": (a.get("learning_stage_info") or {}).get("status"),
                    "learning_conversions": (a.get("learning_stage_info") or {}).get("conversions"),
                    "attribution": _attribution_label(a.get("attribution_spec")),
                    "targeting": _targeting_summary(a),
                    "metrics": _round_metrics(a["metrics"]),
                }
                for a in rows[: args.get("limit", 25)]
            ],
        }

    if name == "list_ads":
        rows = [a for a in snapshot["ads"] if _match_status(a, args.get("status"))]
        if args.get("adset_id"):
            rows = [a for a in rows if a.get("adset_id") == args["adset_id"]]
        if args.get("campaign_id"):
            rows = [a for a in rows if a.get("campaign_id") == args["campaign_id"]]
        if args.get("min_impressions") is not None:
            rows = [
                a for a in rows
                if a["metrics"].get("impressions", 0) >= args["min_impressions"]
            ]
        rows.sort(key=_sort_key("spend"))
        return {
            "currency": currency,
            "count": len(rows),
            "ads": [
                {
                    "id": a["id"],
                    "name": a.get("name"),
                    "adset_id": a.get("adset_id"),
                    "campaign_id": a.get("campaign_id"),
                    "status": a.get("status"),
                    "creative_type": (a.get("creative") or {}).get("object_type"),
                    "creative_name": (a.get("creative") or {}).get("name"),
                    "quality_ranking": (a.get("insights") or {}).get("quality_ranking"),
                    "engagement_rate_ranking": (a.get("insights") or {}).get("engagement_rate_ranking"),
                    "conversion_rate_ranking": (a.get("insights") or {}).get("conversion_rate_ranking"),
                    "metrics": _round_metrics(a["metrics"]),
                }
                for a in rows[: args.get("limit", 20)]
            ],
        }

    if name == "get_breakdown":
        rows = await breakdown_fetcher(args["cut"])
        # Annotate thin rows in the data rather than trusting the system
        # prompt's "small samples lie" rule. Observed: given a placement at
        # $2.72 on 4 conversions, the model recommended moving budget into it.
        # A flag on the row itself is much harder to read past than an
        # instruction several thousand tokens earlier.
        if isinstance(rows, list):
            annotated = []
            for row in rows:
                if not isinstance(row, dict):
                    annotated.append(row)
                    continue
                results = float(row.get("results") or 0)
                entry = dict(row)
                if results == 0:
                    entry["reliability"] = "no conversions - cost per result is meaningless"
                elif results < 30:
                    entry["reliability"] = (
                        f"only {int(results)} conversions - too few to rank on cost "
                        "per result; treat as directional, not decisive"
                    )
                else:
                    entry["reliability"] = "sufficient sample"
                annotated.append(entry)
            rows = annotated
        return {
            "cut": args["cut"],
            "currency": currency,
            "note": (
                "Rows with fewer than ~30 conversions are noise. Do not recommend "
                "moving budget INTO a row on the strength of a small sample."
            ),
            "rows": rows,
        }

    if name == "get_audit_findings":
        findings = audit.get("findings", [])
        if args.get("severity"):
            findings = [f for f in findings if f["severity"] == args["severity"]]
        if args.get("category"):
            findings = [
                f for f in findings
                if f["category"].lower() == str(args["category"]).lower()
            ]
        return {
            "currency": currency,
            "health_score": audit.get("score"),
            "total_findings": len(audit.get("findings", [])),
            "counts_by_severity": audit.get("counts"),
            "estimated_recoverable_spend": audit.get("estimatedWaste"),
            "findings": [
                {
                    "severity": f["severity"],
                    "category": f["category"],
                    "title": f["title"],
                    "entity": f["entity"],
                    "detail": f["detail"],
                    "evidence": f.get("evidence"),
                    "recommendation": f["recommendation"],
                    "estimated_impact": f.get("impact"),
                }
                for f in findings[: args.get("limit", 25)]
            ],
        }

    return {"error": f"Unknown tool {name}"}


# ------------------------------------------------------------------- the agent

def _context_header(snapshot: dict, audit: dict) -> str:
    """A small orientation block so the first question does not need a tool call."""
    account = snapshot["account"]
    m = snapshot["accountMetrics"]
    counts = audit.get("counts", {})
    severities = ", ".join(
        f"{counts.get(s, 0)} {s}" for s in SEVERITY_ORDER if counts.get(s)
    )
    return (
        f"Account: {account.get('name')} ({account.get('id')}), "
        f"currency {snapshot.get('currency')}, window {snapshot.get('windowLabel')}.\n"
        f"Spend {m.get('spend', 0):.2f} · {m.get('resultLabel')} {int(m.get('results', 0))} · "
        f"cost per result {m.get('costPerResult', 0):.2f} · CTR {m.get('ctr', 0):.2f}% · "
        f"frequency {m.get('frequency', 0):.2f}.\n"
        f"Audit: health {audit.get('score')}/100, {severities or 'no findings'}, "
        f"estimated recoverable {audit.get('estimatedWaste', 0):.2f}.\n"
        f"Structure: {len(snapshot['campaigns'])} campaigns, "
        f"{len(snapshot['adsets'])} ad sets, {len(snapshot['ads'])} ads.\n"
        "Use tools for anything beyond these headline figures."
    )


class StrategistNotConfigured(RuntimeError):
    pass


def _client() -> openai.AsyncOpenAI:
    """Build the client, preferring .env.local but not requiring it.

    An unset key in .env.local does not mean there are no credentials -- the
    SDK also reads OPENAI_API_KEY from the environment.
    """
    key = get_settings().openai_api_key
    try:
        return openai.AsyncOpenAI(api_key=key) if key else openai.AsyncOpenAI()
    except Exception as cause:  # noqa: BLE001 - surfaced to the user as guidance
        raise StrategistNotConfigured(
            "No OpenAI credentials found. Set OPENAI_API_KEY in .env.local and "
            "restart the API."
        ) from cause


def _collect_tool_calls(acc: dict[int, dict], delta_tool_calls) -> None:
    """Accumulate streamed tool calls.

    A tool call arrives across many chunks: the first carries `id` and the
    function name, later ones carry fragments of the argument JSON. They are
    keyed by `index`, not by id -- the id is absent from the fragments, and
    several calls can stream interleaved in one turn.
    """
    for tc in delta_tool_calls or []:
        slot = acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
        if tc.id:
            slot["id"] = tc.id
        if tc.function and tc.function.name:
            slot["name"] = tc.function.name
        if tc.function and tc.function.arguments:
            slot["arguments"] += tc.function.arguments


async def stream_reply(
    history: list[dict],
    *,
    snapshot: dict,
    audit: dict,
    breakdown_fetcher,
    lang: str = "en",
) -> AsyncIterator[dict]:
    """Run one agent turn, yielding UI events until the model stops calling tools.

    Yields dicts: {"type": "thinking"|"text"|"tool"|"tool_done"|"done"|"error"}.
    A manual loop rather than a framework, because tool activity is streamed to
    the browser as it happens -- the user should see "reading placements" while
    it reads placements, not a spinner.
    """
    settings = get_settings()
    client = _client()

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "system",
            "content": f"Account context:\n{_context_header(snapshot, audit)}",
        },
        # The interface language. Tool data stays English; the answer does not.
        *([{"role": "system", "content": (
            "Answer in French (professional Moroccan business register, vouvoiement), "
            "whatever language the account data or tool results are in. Keep campaign "
            "and ad names exactly as they are."
        )}] if lang == "fr" else []),
        *history,
    ]

    # A hard stop on tool rounds. Without it a confused model can ping-pong
    # tool calls until the request times out, with the user watching a spinner.
    for _ in range(12):
        pending: dict[int, dict] = {}
        assistant_text = ""
        finish_reason = None
        announced: set[int] = set()

        try:
            stream = await client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                tools=TOOLS,
                stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta

                if delta and delta.content:
                    assistant_text += delta.content
                    yield {"type": "text", "text": delta.content}

                if delta and delta.tool_calls:
                    _collect_tool_calls(pending, delta.tool_calls)
                    # Announce each call once, as soon as its name is known, so
                    # the UI can show what it is doing while arguments stream.
                    for index, slot in pending.items():
                        if slot["name"] and index not in announced:
                            announced.add(index)
                            yield {"type": "tool", "name": slot["name"]}

                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        except openai.APIStatusError as error:
            yield {"type": "error", "message": f"OpenAI API error ({error.status_code}): {error.message}"}
            return
        except openai.APIConnectionError as error:
            yield {"type": "error", "message": f"Could not reach the OpenAI API: {error}"}
            return

        calls = [pending[i] for i in sorted(pending) if pending[i]["name"]]

        if finish_reason != "tool_calls" or not calls:
            if finish_reason == "length" and not assistant_text:
                yield {"type": "error", "message": "The reply hit the model's output limit."}
                return
            yield {"type": "done"}
            return

        messages.append({
            "role": "assistant",
            "content": assistant_text or None,
            "tool_calls": [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {"name": c["name"], "arguments": c["arguments"] or "{}"},
                }
                for c in calls
            ],
        })

        for call in calls:
            try:
                # Streamed arguments can be truncated or malformed; a bad parse
                # is reported to the model as a tool error so it can retry,
                # rather than raising and killing the stream.
                args = json.loads(call["arguments"] or "{}")
                if not isinstance(args, dict):
                    raise ValueError("tool arguments were not an object")
                payload = await execute_tool(
                    call["name"], args,
                    snapshot=snapshot, audit=audit, breakdown_fetcher=breakdown_fetcher,
                )
                content = json.dumps(payload, default=str)
            except json.JSONDecodeError as cause:
                content = f"Tool arguments were not valid JSON: {cause}. Call the tool again."
            except Exception as cause:  # noqa: BLE001 - reported to the model, not hidden
                content = f"Tool failed: {type(cause).__name__}: {cause}"

            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": content,
            })
            yield {"type": "tool_done", "name": call["name"]}

    yield {"type": "error", "message": "Stopped after 12 tool rounds without a final answer."}
