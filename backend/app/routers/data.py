"""Data routes. Every one of them requires a session."""

from __future__ import annotations

import csv
import io

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse

from ..audit.engine import run_audit, summarise
from ..auth import require_session
from ..cache import audit_cache, breakdown_cache
from ..portfolio import MAX_ACCOUNTS, build_portfolio
from ..report import build_report
from ..meta.client import MetaApiError, debug_token
from ..meta.collect import (
    collect_account,
    fetch_breakdown,
    list_ad_accounts,
    normalise_account_id,
)
from ..meta.window import resolve_window

router = APIRouter(prefix="/api", tags=["data"], dependencies=[Depends(require_session)])

# Breakdown cuts the dashboard offers. Meta rejects arbitrary combinations, so
# these are the validated ones rather than a free-text passthrough.
BREAKDOWNS = {
    "placement": "publisher_platform,platform_position",
    "platform": "publisher_platform",
    "device": "impression_device",
    "age": "age",
    "gender": "gender",
    "age_gender": "age,gender",
    "country": "country",
    "region": "region",
    "hour": "hourly_stats_aggregated_by_advertiser_time_zone",
}


def _window(preset: str | None, since: str | None, until: str | None):
    """Resolve query params into a Window, turning bad input into a 400."""
    try:
        return resolve_window(preset, since, until)
    except ValueError as cause:
        raise HTTPException(status_code=400, detail={"message": str(cause)}) from cause


def _handle(error: MetaApiError) -> HTTPException:
    if error.is_auth_error:
        status = 401
    elif error.is_rate_limit:
        # The app is on development_access, whose ads_insights budget is small.
        # Saying so beats a generic 502 the user cannot act on.
        status = 429
    else:
        status = 502
    message = error.message
    if error.is_rate_limit:
        message += (
            " — Meta is throttling this app. Cached results are served for 5 minutes; "
            "request Advanced Access for ads_read to raise the limit."
        )
    return HTTPException(
        status_code=status,
        detail={"message": message, "code": error.code, "subcode": error.subcode},
    )


@router.get("/accounts")
async def accounts():
    try:
        rows = await list_ad_accounts()
        token = await debug_token()
        return {"accounts": rows, "token": token}
    except MetaApiError as error:
        raise _handle(error) from error


@router.get("/audit/{account_id}")
async def audit(
    account_id: str,
    preset: str | None = Query(None),
    since: str | None = Query(None, description="YYYY-MM-DD, with until"),
    until: str | None = Query(None, description="YYYY-MM-DD, with since"),
    fresh: bool = Query(False, description="Bypass the 5-minute cache"),
    lang: str = Query("en", pattern="^(en|fr)$", description="Language of the findings"),
):
    """The full payload the dashboard renders: snapshot + audit + trend."""
    window = _window(preset, since, until)
    try:
        snapshot, age = await audit_cache.get_or_set(
            f"{account_id}:{window.key}",
            lambda: collect_account(account_id, window),
            fresh=fresh,
        )
    except MetaApiError as error:
        raise _handle(error) from error

    result = run_audit(snapshot, lang)
    summary = summarise(snapshot, result)

    # Only what the UI renders crosses the wire. The raw snapshot carries full
    # targeting specs for every ad set and would be an order of magnitude
    # larger for no benefit.
    def trim(entity: dict, extra: dict | None = None) -> dict:
        return {
            "id": entity["id"],
            "name": entity.get("name"),
            "status": entity.get("status"),
            "effectiveStatus": entity.get("effective_status"),
            "metrics": entity["metrics"],
            **(extra or {}),
        }

    return {
        "summary": summary,
        "audit": result,
        "cache": {"ageSeconds": round(age, 1), "fromCache": age > 0},
        "warnings": snapshot.get("warnings", []),
        "window": {
            "key": window.key,
            "label": window.label,
            "since": window.since.isoformat() if window.since else None,
            "until": window.until.isoformat() if window.until else None,
            "days": window.days,
            "hasComparison": window.previous() is not None,
        },
        "trend": snapshot["trend"],
        "account": {
            "id": snapshot["account"].get("id"),
            "name": snapshot["account"].get("name"),
            "currency": snapshot["currency"],
            "timezone": snapshot["account"].get("timezone_name"),
            "business": (snapshot["account"].get("business") or {}).get("name"),
            "accountStatus": snapshot["account"].get("account_status"),
            "balance": snapshot["account"].get("balance"),
        },
        "campaigns": [
            trim(c, {
                "objective": c.get("objective"),
                "dailyBudget": float(c.get("daily_budget") or 0) / 100,
                "lifetimeBudget": float(c.get("lifetime_budget") or 0) / 100,
                "bidStrategy": c.get("bid_strategy"),
                "createdTime": c.get("created_time"),
            })
            for c in snapshot["campaigns"]
        ],
        "adsets": [
            trim(a, {
                "campaignId": a.get("campaign_id"),
                "objective": a.get("objective"),
                "dailyBudget": float(a.get("daily_budget") or 0) / 100,
                "optimizationGoal": a.get("optimization_goal"),
                "learningStage": (a.get("learning_stage_info") or {}).get("status"),
            })
            for a in snapshot["adsets"]
        ],
        "ads": [
            trim(a, {
                "adsetId": a.get("adset_id"),
                "campaignId": a.get("campaign_id"),
                "qualityRanking": (a.get("insights") or {}).get("quality_ranking"),
                "thumbnail": (a.get("creative") or {}).get("thumbnail_url"),
            })
            for a in snapshot["ads"]
        ],
    }


@router.get("/portfolio")
async def portfolio(
    accounts: str = Query(..., description="Comma-separated act_ ids"),
    preset: str | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
    fresh: bool = Query(False),
    lang: str = Query("en", pattern="^(en|fr)$", description="Language of the findings"),
):
    """Several accounts audited together, with currency-safe totals."""
    ids = [a.strip() for a in accounts.split(",") if a.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail={"message": "No accounts given"})
    if len(ids) > MAX_ACCOUNTS:
        raise HTTPException(
            status_code=400,
            detail={"message": f"At most {MAX_ACCOUNTS} accounts can be compared at once"},
        )

    window = _window(preset, since, until)
    key = f"portfolio:{','.join(sorted(ids))}:{window.key}:{lang}"
    try:
        # Per-account snapshots are cached individually inside collect, but the
        # merged result is cached too so paging between tabs is free.
        result, age = await audit_cache.get_or_set(
            key, lambda: build_portfolio(ids, window, lang), fresh=fresh
        )
    except MetaApiError as error:
        raise _handle(error) from error

    return {**result, "cache": {"ageSeconds": round(age, 1), "fromCache": age > 0}}


@router.get("/breakdown/{account_id}")
async def breakdown(
    account_id: str,
    cut: str = Query("placement"),
    preset: str | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
    fresh: bool = Query(False, description="Bypass the 5-minute cache"),
):
    if cut not in BREAKDOWNS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown breakdown '{cut}'. Available: {', '.join(sorted(BREAKDOWNS))}",
        )
    window = _window(preset, since, until)

    async def produce():
        async with httpx.AsyncClient(timeout=90.0) as client:
            return await fetch_breakdown(
                normalise_account_id(account_id), window, BREAKDOWNS[cut], client
            )

    try:
        rows, age = await breakdown_cache.get_or_set(
            f"{account_id}:{cut}:{window.key}", produce, fresh=fresh
        )
        return {
            "cut": cut,
            "dimensions": BREAKDOWNS[cut],
            "rows": rows,
            "cache": {"ageSeconds": round(age, 1), "fromCache": age > 0},
        }
    except MetaApiError as error:
        raise _handle(error) from error


@router.get("/export/{account_id}.csv")
async def export_csv(
    account_id: str,
    preset: str | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
    lang: str = Query("en", pattern="^(en|fr)$", description="Language of the findings"),
):
    window = _window(preset, since, until)
    try:
        # Shares the audit cache: exporting right after viewing costs no API budget.
        snapshot, _ = await audit_cache.get_or_set(
            f"{account_id}:{window.key}", lambda: collect_account(account_id, window)
        )
    except MetaApiError as error:
        raise _handle(error) from error

    result = run_audit(snapshot, lang)
    summary = summarise(snapshot, result)

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(["# account", summary["accountName"]])
    writer.writerow(["# account_id", summary["accountId"]])
    writer.writerow(["# window", window.label])
    writer.writerow(["# health_score", summary["score"]])
    writer.writerow(["# estimated_waste", summary["estimatedWaste"]])
    writer.writerow(["# currency", summary["currency"]])
    writer.writerow([])
    writer.writerow([
        "severity", "category", "rule", "title", "level",
        "entity_id", "entity_name", "detail", "recommendation", "estimated_impact",
    ])
    for f in result["findings"]:
        writer.writerow([
            f["severity"], f["category"], f["ruleId"], f["title"], f["entity"]["level"],
            f["entity"]["id"], f["entity"]["name"], f["detail"], f["recommendation"],
            f"{f.get('impact', 0):.2f}",
        ])

    buffer.seek(0)
    filename = f"audit-{account_id}-{window.key.replace(':', '-')}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/report/{account_id}.pdf")
async def export_pdf(
    account_id: str,
    preset: str | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
    lang: str = Query("fr", pattern="^(fr|en)$"),
):
    """Designed PDF report: cover, KPIs vs previous period, trend charts,
    campaigns, best and wasted ads, and the audit with recommendations."""
    window = _window(preset, since, until)
    try:
        # Same cache as the dashboard: exporting what is on screen costs no API budget.
        snapshot, _ = await audit_cache.get_or_set(
            f"{account_id}:{window.key}", lambda: collect_account(account_id, window)
        )
    except MetaApiError as error:
        raise _handle(error) from error

    pdf = build_report(snapshot, run_audit(snapshot, lang), window_label=window.label, lang=lang)
    name = (snapshot["account"].get("name") or account_id).strip()
    slug = "".join(ch if ch.isalnum() else "-" for ch in name).strip("-").lower()[:40] or "compte"
    filename = f"rapport-meta-{slug}-{window.key.replace(':', '-')}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
