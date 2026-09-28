"""
Pulls the full picture of one ad account: structure, performance, trends,
and audience/placement breakdowns.

Performance uses level-scoped insights calls (/act_X/insights?level=ad) rather
than one call per entity. An account with 40 campaigns / 90 ad sets / 200 ads
is 3 requests this way instead of 330 -- the difference between an audit that
finishes inside a request and one that trips Meta's hourly rate limit.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import get_settings
from .client import MetaApiError, graph_get, graph_get_all
from .metrics import derive_metrics
from .window import Window, resolve_window

# `business{id,name}` needs the business_management permission, which a
# least-privilege System User token often does not carry. It is only ever a
# label in the UI, so it is requested separately and dropped when refused --
# demanding a broader token for a subtitle would be the wrong trade.
ACCOUNT_CORE_FIELDS = ",".join([
    "id", "account_id", "name", "account_status", "currency", "timezone_name",
    "amount_spent", "balance", "spend_cap",
    "funding_source_details", "disable_reason", "created_time", "is_prepay_account",
])
ACCOUNT_FIELDS = ACCOUNT_CORE_FIELDS + ",business{id,name}"


def _is_permission_error(error: Exception) -> bool:
    """A missing-permission refusal, as opposed to a bad token or a bad id."""
    return getattr(error, "code", None) == 100 and "permission" in str(error).lower()


async def _fetch_account(act: str, client: httpx.AsyncClient) -> dict:
    """Account fields, retrying without the business edge if it is refused."""
    try:
        return await graph_get(act, {"fields": ACCOUNT_FIELDS}, client=client)
    except MetaApiError as error:
        if not _is_permission_error(error):
            raise
        return await graph_get(act, {"fields": ACCOUNT_CORE_FIELDS}, client=client)

CAMPAIGN_FIELDS = ",".join([
    "id", "name", "status", "effective_status", "objective", "buying_type",
    "daily_budget", "lifetime_budget", "budget_remaining", "bid_strategy",
    "created_time", "updated_time", "start_time", "stop_time",
    "special_ad_categories", "issues_info",
])

ADSET_FIELDS = ",".join([
    "id", "name", "campaign_id", "status", "effective_status", "daily_budget",
    "lifetime_budget", "budget_remaining", "bid_strategy", "bid_amount",
    "billing_event", "optimization_goal", "attribution_spec", "targeting",
    "start_time", "end_time", "created_time", "updated_time",
    "learning_stage_info", "destination_type", "promoted_object", "issues_info",
])

AD_FIELDS = ",".join([
    "id", "name", "adset_id", "campaign_id", "status", "effective_status",
    "created_time", "updated_time",
    "creative{id,name,object_type,thumbnail_url,effective_object_story_id}",
    "issues_info",
])

INSIGHT_FIELDS = ",".join([
    "spend", "impressions", "reach", "frequency", "clicks", "ctr", "cpc", "cpm",
    "actions", "action_values", "purchase_roas", "quality_ranking",
    "engagement_rate_ranking", "conversion_rate_ranking",
])

LEVEL_ID_FIELD = {"campaign": "campaign_id", "adset": "adset_id", "ad": "ad_id"}



def normalise_account_id(account_id: str) -> str:
    return account_id if account_id.startswith("act_") else f"act_{account_id}"


async def _insights_by_level(
    account_id: str, level: str, window: Window, client: httpx.AsyncClient
) -> dict[str, dict]:
    """Insights keyed by entity id, for one level.

    The id field has to be named explicitly in ``fields``. A level=adset call
    does NOT return adset_id unless you ask for it -- the rows come back as
    bare metrics, every one joins to None, and every entity ends up with empty
    metrics. Nothing errors; the audit just silently reports zeros. That is why
    the id is appended here rather than left to the caller.
    """
    id_field = LEVEL_ID_FIELD[level]
    rows = await graph_get_all(
        f"{account_id}/insights",
        {
            "level": level,
            **window.params,
            "fields": f"{id_field},{INSIGHT_FIELDS}",
            "limit": 200,
        },
        client=client,
    )
    return {row[id_field]: row for row in rows if row.get(id_field)}


async def _safe(coro, fallback, *, source: str, warnings: list[dict]):
    """Run a sub-fetch, recording rather than hiding a failure.

    These calls are individually optional -- an audit is still worth showing
    without, say, the previous-period comparison. But swallowing the error
    outright is actively misleading: if the ad-level insights call is
    rate-limited, every ad silently gets zero metrics, the creative rules stop
    firing, and the health score goes *up*. The caller surfaces `warnings` so
    the UI can say the picture is incomplete instead of quietly scoring the
    account better than it is.
    """
    try:
        return await coro
    except Exception as cause:  # noqa: BLE001 - recorded, not hidden
        warnings.append({
            "source": source,
            "message": getattr(cause, "message", None) or str(cause),
            "code": getattr(cause, "code", None),
        })
        return fallback


async def fetch_trend(
    account_id: str, window: Window, client: httpx.AsyncClient
) -> list[dict]:
    """Day-by-day series, so the dashboard can plot spend and cost per result."""
    rows = await graph_get_all(
        f"{account_id}/insights",
        {
            **window.params,
            "fields": "spend,impressions,reach,clicks,actions",
            "time_increment": 1,
            "limit": 200,
        },
        client=client,
    )
    out = []
    for row in rows:
        metrics = derive_metrics(row, "OUTCOME_LEADS")
        out.append({
            "date": row.get("date_start"),
            "spend": metrics["spend"],
            "impressions": metrics["impressions"],
            "clicks": metrics["clicks"],
            "results": metrics["results"],
            "costPerResult": metrics["costPerResult"],
            "ctr": metrics["ctr"],
            "cpm": metrics["cpm"],
        })
    return out


async def fetch_breakdown(
    account_id: str, window: Window, breakdowns: str, client: httpx.AsyncClient
) -> list[dict]:
    """One breakdown cut (placement, device, age/gender, country).

    Breakdown rows carry the dimension values as extra keys rather than a
    nested object, so the dimension keys are whatever was asked for.
    """
    dimensions = breakdowns.split(",")
    rows = await graph_get_all(
        f"{account_id}/insights",
        {
            **window.params,
            "fields": "spend,impressions,reach,clicks,actions",
            "breakdowns": breakdowns,
            "limit": 200,
        },
        client=client,
    )
    out = []
    for row in rows:
        metrics = derive_metrics(row, "OUTCOME_LEADS")
        label = " · ".join(str(row.get(d, "unknown")) for d in dimensions)
        out.append({
            "key": label,
            "dimensions": {d: row.get(d) for d in dimensions},
            "spend": metrics["spend"],
            "impressions": metrics["impressions"],
            "clicks": metrics["clicks"],
            "results": metrics["results"],
            "costPerResult": metrics["costPerResult"],
            "ctr": metrics["ctr"],
            "cpm": metrics["cpm"],
        })
    out.sort(key=lambda r: r["spend"], reverse=True)
    return out


async def fetch_previous_period(
    account_id: str, window: Window, client: httpx.AsyncClient
) -> dict[str, Any]:
    """Metrics for the equal-length window immediately before this one."""
    params = window.previous()
    if not params:
        return {}
    body = await graph_get(
        f"{account_id}/insights", {**params, "fields": INSIGHT_FIELDS}, client=client
    )
    rows = body.get("data") or []
    if not rows:
        return {}
    return derive_metrics(rows[0], "OUTCOME_LEADS")


async def collect_account(
    account_id: str, window: Window | str = "last_30d"
) -> dict[str, Any]:
    """Everything the audit engine and dashboard need, in one object."""
    act = normalise_account_id(account_id)
    if isinstance(window, str):  # convenience for scripts and tests
        window = resolve_window(window)

    warnings: list[dict] = []

    async with httpx.AsyncClient(timeout=90.0) as client:
        (
            account,
            campaigns,
            adsets,
            ads,
            campaign_insights,
            adset_insights,
            ad_insights,
            account_summary,
            trend,
            previous,
        ) = await asyncio.gather(
            _fetch_account(act, client),
            graph_get_all(f"{act}/campaigns", {"fields": CAMPAIGN_FIELDS}, client=client),
            graph_get_all(f"{act}/adsets", {"fields": ADSET_FIELDS}, client=client),
            graph_get_all(f"{act}/ads", {"fields": AD_FIELDS}, client=client),
            _safe(_insights_by_level(act, "campaign", window, client), {}, source="campaign_insights", warnings=warnings),
            _safe(_insights_by_level(act, "adset", window, client), {}, source="adset_insights", warnings=warnings),
            _safe(_insights_by_level(act, "ad", window, client), {}, source="ad_insights", warnings=warnings),
            _safe(
                graph_get(
                    f"{act}/insights",
                    {**window.params, "fields": INSIGHT_FIELDS},
                    client=client,
                ),
                {"data": []},
                source="account_summary",
                warnings=warnings,
            ),
            _safe(fetch_trend(act, window, client), [], source="trend", warnings=warnings),
            _safe(
                fetch_previous_period(act, window, client),
                {},
                source="previous_period",
                warnings=warnings,
            ),
        )

    # Objective lives on the campaign but is needed to read results at every
    # level, so it is pushed down the tree here rather than looked up in rules.
    objective_by_campaign = {c["id"]: c.get("objective") for c in campaigns}

    for campaign in campaigns:
        row = campaign_insights.get(campaign["id"], {})
        campaign["insights"] = row
        campaign["metrics"] = derive_metrics(row, campaign.get("objective"))

    for adset in adsets:
        objective = objective_by_campaign.get(adset.get("campaign_id")) or "OUTCOME_LEADS"
        row = adset_insights.get(adset["id"], {})
        adset["objective"] = objective
        adset["insights"] = row
        adset["metrics"] = derive_metrics(row, objective)

    for ad in ads:
        objective = objective_by_campaign.get(ad.get("campaign_id")) or "OUTCOME_LEADS"
        row = ad_insights.get(ad["id"], {})
        ad["objective"] = objective
        ad["insights"] = row
        ad["metrics"] = derive_metrics(row, objective)

    summary_rows = account_summary.get("data") or []
    summary_row = summary_rows[0] if summary_rows else {}

    return {
        "account": account,
        "campaigns": campaigns,
        "adsets": adsets,
        "ads": ads,
        "accountMetrics": derive_metrics(summary_row, "OUTCOME_LEADS"),
        "previousMetrics": previous,
        "trend": trend,
        "datePreset": window.key,
        "windowLabel": window.label,
        "windowSince": window.since.isoformat() if window.since else None,
        "windowUntil": window.until.isoformat() if window.until else None,
        "currency": account.get("currency", "USD"),
        # Non-empty means some sub-fetch failed and the audit below it is
        # incomplete -- the UI warns rather than presenting a clean score.
        "warnings": warnings,
        "collectedAt": datetime.now(timezone.utc).isoformat(),
    }


async def list_ad_accounts() -> list[dict]:
    """Ad accounts this token can see, highest lifetime spend first."""
    base = ("id,account_id,name,account_status,currency,timezone_name,"
            "amount_spent,balance")
    try:
        rows = await graph_get_all("me/adaccounts", {"fields": base + ",business{id,name}"})
    except MetaApiError as error:
        if not _is_permission_error(error):
            raise
        rows = await graph_get_all("me/adaccounts", {"fields": base})
    allowed = get_settings().allowed_accounts
    if allowed:
        rows = [r for r in rows if r.get("id") in allowed]
    rows.sort(key=lambda r: float(r.get("amount_spent") or 0), reverse=True)
    return rows
