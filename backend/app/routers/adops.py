"""
Ad manager and budget routes — the only write path in the app.

Plan and apply are separate endpoints on purpose. `/plan` never mutates, so the
UI can show exactly what will change and the user confirms against real
before/after values. `/apply` re-plans from live data and refuses if the
fingerprint has moved.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..adops import OpsError, apply_plan, budget_overview, build_plan, read_log
from ..audit.engine import run_audit
from ..auth import require_session
from ..cache import audit_cache
from ..meta.client import MetaApiError
from ..meta.collect import collect_account
from ..meta.window import resolve_window

router = APIRouter(prefix="/api/ops", tags=["ops"], dependencies=[Depends(require_session)])


class ChangeRequest(BaseModel):
    id: str
    action: str = Field(pattern="^(pause|resume|set_daily_budget)$")
    value: float | None = None


class PlanRequest(BaseModel):
    account_id: str
    changes: list[ChangeRequest] = Field(min_length=1, max_length=50)
    preset: str | None = None
    since: str | None = None
    until: str | None = None


class ApplyRequest(PlanRequest):
    fingerprint: str
    dry_run: bool = False


async def _snapshot(body: PlanRequest, *, fresh: bool = False):
    try:
        window = resolve_window(body.preset, body.since, body.until)
    except ValueError as cause:
        raise HTTPException(status_code=400, detail={"message": str(cause)}) from cause
    try:
        snapshot, _ = await audit_cache.get_or_set(
            f"{body.account_id}:{window.key}",
            lambda: collect_account(body.account_id, window),
            fresh=fresh,
        )
        return snapshot
    except MetaApiError as error:
        raise HTTPException(
            status_code=401 if error.is_auth_error else 502,
            detail={"message": error.message, "code": error.code},
        ) from error


@router.post("/plan")
async def plan(body: PlanRequest):
    """Preview changes. Guaranteed not to mutate anything."""
    snapshot = await _snapshot(body)
    try:
        return build_plan(snapshot, [c.model_dump() for c in body.changes])
    except OpsError as cause:
        raise HTTPException(status_code=400, detail={"message": str(cause)}) from cause


@router.post("/apply")
async def apply(body: ApplyRequest, user: str = Depends(require_session)):
    """Apply a reviewed plan.

    Always re-plans against *fresh* data: a cached snapshot could be five
    minutes stale, and five minutes is long enough for someone else to have
    changed a budget in Ads Manager.
    """
    snapshot = await _snapshot(body, fresh=True)
    try:
        return await apply_plan(
            snapshot,
            [c.model_dump() for c in body.changes],
            body.fingerprint,
            user=user,
            dry_run=body.dry_run,
        )
    except OpsError as cause:
        raise HTTPException(status_code=409, detail={"message": str(cause)}) from cause
    except MetaApiError as error:
        raise HTTPException(
            status_code=502, detail={"message": error.message, "code": error.code}
        ) from error


@router.get("/budget/{account_id}")
async def budget(
    account_id: str,
    preset: str | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
):
    """Budget allocation beside what each line is producing."""
    body = PlanRequest(
        account_id=account_id,
        changes=[ChangeRequest(id="_", action="pause")],  # unused; shape only
        preset=preset, since=since, until=until,
    )
    snapshot = await _snapshot(body)
    audit = run_audit(snapshot)
    overview = budget_overview(snapshot)
    # Budget-relevant findings, so the page can say why a line is flagged.
    overview["findings"] = [
        {
            "severity": f["severity"], "title": f["title"], "entity": f["entity"],
            "detail": f["detail"], "recommendation": f["recommendation"],
            "impact": f.get("impact", 0),
        }
        for f in audit["findings"]
        if f["category"] in ("Budget & pacing", "Performance", "Delivery")
    ][:12]
    return overview


@router.get("/log")
def log(limit: int = Query(100, ge=1, le=500)):
    """Every write this app has made, newest first."""
    return {"entries": read_log(limit)}
