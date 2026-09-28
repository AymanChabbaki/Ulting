"""
Normalises Meta insights rows into the handful of numbers an audit reasons about.

The hard part is ``actions``: Graph returns 30+ action_type entries per row
(one real last-30d row here carried 33), and which one counts as "the
conversion" depends entirely on the campaign objective. A leads campaign
reporting 6,218 link_clicks and 79 leads has a CPL of spend/79 -- counting the
clicks instead understates cost per result by ~80x.
"""

from __future__ import annotations

from typing import Any, Iterable

# Objective -> the action types that represent a real result, best first.
# The first type present wins rather than summing: Meta reports the same
# conversion under several aliases (a pixel lead appears as `lead`,
# `onsite_web_lead` AND `offsite_conversion.fb_pixel_lead` at once), so summing
# would triple-count it.
RESULT_ACTIONS: dict[str, list[str]] = {
    "OUTCOME_LEADS": [
        "lead",
        "onsite_web_lead",
        "offsite_conversion.fb_pixel_lead",
        "onsite_conversion.lead_grouped",
        "onsite_conversion.messaging_conversation_started_7d",
    ],
    "OUTCOME_SALES": [
        "purchase",
        "omni_purchase",
        "offsite_conversion.fb_pixel_purchase",
        "onsite_conversion.purchase",
    ],
    "OUTCOME_TRAFFIC": ["landing_page_view", "omni_landing_page_view", "link_click"],
    "OUTCOME_ENGAGEMENT": [
        "onsite_conversion.messaging_conversation_started_7d",
        "post_engagement",
        "page_engagement",
    ],
    "OUTCOME_AWARENESS": ["reach"],
    "OUTCOME_APP_PROMOTION": ["omni_app_install", "app_install", "mobile_app_install"],
    # Legacy objective names, still attached to older campaigns in most accounts.
    "LEAD_GENERATION": ["lead", "onsite_conversion.lead_grouped"],
    "CONVERSIONS": ["offsite_conversion.fb_pixel_purchase", "purchase", "lead"],
    "LINK_CLICKS": ["link_click"],
    "POST_ENGAGEMENT": ["post_engagement"],
    "MESSAGES": ["onsite_conversion.messaging_conversation_started_7d"],
}

RESULT_LABELS: dict[str, str] = {
    "OUTCOME_LEADS": "Leads",
    "OUTCOME_SALES": "Purchases",
    "OUTCOME_TRAFFIC": "Landing page views",
    "OUTCOME_ENGAGEMENT": "Conversations",
    "OUTCOME_AWARENESS": "Reach",
    "OUTCOME_APP_PROMOTION": "App installs",
    "LEAD_GENERATION": "Leads",
    "CONVERSIONS": "Conversions",
    "LINK_CLICKS": "Link clicks",
    "POST_ENGAGEMENT": "Engagements",
    "MESSAGES": "Conversations",
}


def num(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result and result not in (float("inf"), float("-inf")) else 0.0


def action_value(actions: Iterable[dict] | None, action_type: str) -> float:
    for action in actions or []:
        if action.get("action_type") == action_type:
            return num(action.get("value"))
    return 0.0


def results_for(row: dict, objective: str) -> tuple[float, str | None, str]:
    """Result count for a row, plus which action type produced it.

    The action type is returned so reports can say "79 leads (lead)" rather
    than an unexplained number -- when a client disputes a figure, the action
    type is the first thing you need.
    """
    label = RESULT_LABELS.get(objective, "Results")
    if objective == "OUTCOME_AWARENESS":
        return num(row.get("reach")), "reach", "Reach"

    candidates = RESULT_ACTIONS.get(objective) or RESULT_ACTIONS["OUTCOME_LEADS"]
    actions = row.get("actions") or []
    for action_type in candidates:
        value = action_value(actions, action_type)
        if value > 0:
            return value, action_type, label
    return 0.0, candidates[0] if candidates else None, label


def revenue_for(row: dict) -> float:
    """Revenue attributed to the row, for ROAS. Only meaningful on sales objectives."""
    roas_rows = row.get("purchase_roas") or []
    if roas_rows:
        return num(roas_rows[0].get("value")) * num(row.get("spend"))
    values = row.get("action_values") or []
    for action_type in ("purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"):
        value = action_value(values, action_type)
        if value > 0:
            return value
    return 0.0


def derive_metrics(row: dict | None = None, objective: str = "OUTCOME_LEADS") -> dict[str, Any]:
    """Every KPI the audit rules read, from one insights row.

    Meta already returns ctr/cpc/cpm, but only when there were impressions --
    on a starved ad those keys are simply absent, and a rule comparing
    ``None < threshold`` would blow up or silently pass. Recomputing makes the
    zero explicit.
    """
    row = row or {}
    spend = num(row.get("spend"))
    impressions = num(row.get("impressions"))
    reach = num(row.get("reach"))
    clicks = num(row.get("clicks"))
    link_clicks = action_value(row.get("actions"), "link_click")
    landing_page_views = action_value(row.get("actions"), "landing_page_view")

    results, action_type, label = results_for(row, objective)
    revenue = revenue_for(row)

    return {
        "spend": spend,
        "impressions": impressions,
        "reach": reach,
        "clicks": clicks,
        "linkClicks": link_clicks,
        "landingPageViews": landing_page_views,
        "results": results,
        "resultActionType": action_type,
        "resultLabel": label,
        "revenue": revenue,
        "frequency": impressions / reach if reach > 0 else num(row.get("frequency")),
        "ctr": (clicks / impressions * 100) if impressions > 0 else 0.0,
        "linkCtr": (link_clicks / impressions * 100) if impressions > 0 else 0.0,
        "cpc": spend / clicks if clicks > 0 else 0.0,
        "cpm": (spend / impressions * 1000) if impressions > 0 else 0.0,
        "costPerResult": spend / results if results > 0 else 0.0,
        "resultRate": (results / link_clicks * 100) if link_clicks > 0 else 0.0,
        "roas": revenue / spend if spend > 0 else 0.0,
        # Clicks that never became a page view -- the classic landing page leak.
        "lpvRate": (landing_page_views / link_clicks * 100) if link_clicks > 0 else 0.0,
    }


SUMMABLE = (
    "spend",
    "impressions",
    "reach",
    "clicks",
    "linkClicks",
    "landingPageViews",
    "results",
    "revenue",
)


def sum_metrics(rows: Iterable[dict]) -> dict[str, Any]:
    """Sum a set of derived metrics into one, for roll-ups."""
    total = {key: 0.0 for key in SUMMABLE}
    for row in rows:
        for key in SUMMABLE:
            total[key] += num(row.get(key))

    impressions, reach = total["impressions"], total["reach"]
    clicks, spend = total["clicks"], total["spend"]
    return {
        **total,
        # Reach does not sum (the same person is reached by several ad sets),
        # so frequency from summed reach is an upper bound. Flagged, not hidden.
        "frequency": impressions / reach if reach > 0 else 0.0,
        "ctr": (clicks / impressions * 100) if impressions > 0 else 0.0,
        "linkCtr": (total["linkClicks"] / impressions * 100) if impressions > 0 else 0.0,
        "cpc": spend / clicks if clicks > 0 else 0.0,
        "cpm": (spend / impressions * 1000) if impressions > 0 else 0.0,
        "costPerResult": spend / total["results"] if total["results"] > 0 else 0.0,
        "roas": total["revenue"] / spend if spend > 0 else 0.0,
        "lpvRate": (total["landingPageViews"] / total["linkClicks"] * 100)
        if total["linkClicks"] > 0
        else 0.0,
    }


def median(values: Iterable[float]) -> float:
    """Median of the positive values, for 'this costs 3x the account norm'."""
    ordered = sorted(v for v in values if v and v > 0)
    if not ordered:
        return 0.0
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2
