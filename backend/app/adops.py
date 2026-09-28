"""
Write operations against Meta: pause, resume, and budget changes.

Everything else in this app is read-only. This module is not, so it is built
around three rules.

**Plan, then apply.** A request never mutates. It returns a plan showing the
current value and the proposed value for every entity, plus a fingerprint of
that plan. Applying requires the fingerprint, and the fingerprint is recomputed
from live data at apply time -- so a plan built against numbers that have since
changed is rejected rather than silently applied to a different account state.

**Guardrails are server-side.** A client that forgets to confirm, or is driven
by something other than the UI, still cannot raise a budget by 20x or act on an
entity that does not belong to the account in the request.

**Every apply is logged.** Before and after values, who did it, what Meta said.
An ads tool without an audit trail is one unexplained spend spike away from
being untrustworthy.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx

from .config import get_settings
from .meta.client import GRAPH_HOST, MetaApiError

LOG_FILE = Path(os.environ.get("ULTING_DATA_DIR", Path(__file__).resolve().parents[1])) / "changes.jsonl"

Level = Literal["campaign", "adset", "ad"]
LEVELS = ("campaign", "adset", "ad")

# A budget may not be raised past this multiple of its current value in one
# operation. A fat-fingered 2500 -> 250000 (minor units confusion) is the exact
# mistake this exists to stop.
MAX_BUDGET_MULTIPLE = 5.0
# Nor below this, which on most accounts stops delivery entirely.
MIN_DAILY_BUDGET_MAJOR = 1.0


class OpsError(RuntimeError):
    """A guardrail refusal. Distinct from a Meta API failure."""


def _token_and_version() -> tuple[str, str]:
    settings = get_settings()
    if not settings.meta_access_token:
        raise OpsError("META_ACCESS_TOKEN is not set.")
    return settings.meta_access_token, settings.graph_api_version


async def _graph_post(path: str, params: dict[str, Any], *, validate_only: bool = False) -> dict:
    """POST to Graph. `validate_only` asks Meta to check without changing."""
    token, version = _token_and_version()
    body = {k: v for k, v in params.items() if v is not None}
    body["access_token"] = token
    if validate_only:
        body["execution_options"] = json.dumps(["validate_only"])

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(f"{GRAPH_HOST}/{version}/{path.lstrip('/')}", data=body)

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if not response.is_success or "error" in payload:
        error = payload.get("error", {})
        raise MetaApiError(
            error.get("message") or f"Graph returned {response.status_code}",
            status=response.status_code,
            code=error.get("code"),
            subcode=error.get("error_subcode"),
            path=path,
        )
    return payload


# ------------------------------------------------------------------ the plan

def _entity_index(snapshot: dict) -> dict[str, tuple[str, dict]]:
    """Every entity in the account, keyed by id, with its level.

    Built from the snapshot rather than trusting the client: an id that is not
    in here does not belong to this account, and the operation is refused.
    """
    index: dict[str, tuple[str, dict]] = {}
    for level, key in (("campaign", "campaigns"), ("adset", "adsets"), ("ad", "ads")):
        for entity in snapshot.get(key, []):
            index[entity["id"]] = (level, entity)
    return index


def _minor(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_plan(snapshot: dict, requests: list[dict]) -> dict[str, Any]:
    """Turn requested changes into a reviewable plan. Mutates nothing."""
    index = _entity_index(snapshot)
    currency = snapshot.get("currency", "USD")
    items: list[dict] = []

    for request in requests:
        entity_id = str(request.get("id", ""))
        action = request.get("action")

        if entity_id not in index:
            raise OpsError(
                f"{entity_id} is not in this ad account. Refusing to act on it."
            )
        level, entity = index[entity_id]
        name = entity.get("name") or entity_id

        if action in ("pause", "resume"):
            target = "PAUSED" if action == "pause" else "ACTIVE"
            current = entity.get("status")
            items.append({
                "id": entity_id, "level": level, "name": name, "action": action,
                "field": "status", "from": current, "to": target,
                "noop": current == target,
            })

        elif action == "set_daily_budget":
            amount = request.get("value")
            if amount is None:
                raise OpsError(f"{name}: no budget value given.")
            amount = float(amount)
            current_major = _minor(entity.get("daily_budget")) / 100

            if amount < MIN_DAILY_BUDGET_MAJOR:
                raise OpsError(
                    f"{name}: {amount:.2f} {currency}/day is below the "
                    f"{MIN_DAILY_BUDGET_MAJOR:.2f} floor; that would stop delivery."
                )
            if current_major > 0 and amount > current_major * MAX_BUDGET_MULTIPLE:
                raise OpsError(
                    f"{name}: raising the daily budget from {current_major:.2f} to "
                    f"{amount:.2f} {currency} is more than {MAX_BUDGET_MULTIPLE:g}x in one "
                    "step. Do it in stages, or change it in Ads Manager if that is "
                    "really intended."
                )
            if level == "ad":
                raise OpsError(f"{name}: ads do not carry a budget; set it on the ad set.")
            if current_major == 0 and _minor(entity.get("lifetime_budget")) > 0:
                raise OpsError(
                    f"{name} uses a lifetime budget. Switching budget type is not "
                    "supported here -- change it in Ads Manager."
                )

            items.append({
                "id": entity_id, "level": level, "name": name, "action": action,
                "field": "daily_budget", "from": round(current_major, 2),
                "to": round(amount, 2), "currency": currency,
                # Meta takes minor units. Rounding here, once, is what keeps a
                # 25.00 from arriving as 2499.9999.
                "to_minor": int(round(amount * 100)),
                "noop": abs(current_major - amount) < 0.005,
            })
        else:
            raise OpsError(f"Unknown action '{action}'.")

    actionable = [i for i in items if not i["noop"]]
    return {
        "items": items,
        "actionable": len(actionable),
        "noop": len(items) - len(actionable),
        "currency": currency,
        "accountId": snapshot["account"].get("id"),
        "fingerprint": fingerprint(items),
        "builtAt": datetime.now(timezone.utc).isoformat(),
    }


def fingerprint(items: list[dict]) -> str:
    """Stable hash of what is changing, and of the state it was planned against.

    `from` is part of the hash on purpose: if the current value moved between
    plan and apply, the fingerprint no longer matches and the apply is refused.
    """
    material = [
        {k: item[k] for k in ("id", "action", "field", "from", "to")}
        for item in sorted(items, key=lambda i: (i["id"], i["action"]))
    ]
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ----------------------------------------------------------------- the apply

def _log(entry: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, default=str) + "\n")


async def apply_plan(
    snapshot: dict,
    requests: list[dict],
    expected_fingerprint: str,
    *,
    user: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Re-plan against live data, verify the fingerprint, then apply."""
    plan = build_plan(snapshot, requests)

    if plan["fingerprint"] != expected_fingerprint:
        raise OpsError(
            "The account changed since this plan was reviewed, so it was not applied. "
            "Refresh and check the new values before confirming."
        )

    results = []
    for item in plan["items"]:
        if item["noop"]:
            results.append({**item, "status": "skipped", "reason": "already in that state"})
            continue

        params: dict[str, Any] = {}
        if item["field"] == "status":
            params["status"] = item["to"]
        elif item["field"] == "daily_budget":
            params["daily_budget"] = item["to_minor"]

        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "user": user,
            "account": plan["accountId"],
            "level": item["level"],
            "id": item["id"],
            "name": item["name"],
            "field": item["field"],
            "from": item["from"],
            "to": item["to"],
            "dryRun": dry_run,
        }

        try:
            response = await _graph_post(item["id"], params, validate_only=dry_run)
            entry["result"] = "validated" if dry_run else "applied"
            entry["response"] = response
            results.append({**item, "status": entry["result"]})
        except MetaApiError as error:
            entry["result"] = "failed"
            entry["error"] = error.message
            entry["errorCode"] = error.code
            results.append({**item, "status": "failed", "error": error.message})

        _log(entry)

    applied = sum(1 for r in results if r["status"] in ("applied", "validated"))
    return {
        "dryRun": dry_run,
        "applied": applied,
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "results": results,
        "fingerprint": plan["fingerprint"],
    }


def read_log(limit: int = 100) -> list[dict]:
    """Change history, newest first."""
    if not LOG_FILE.exists():
        return []
    lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in reversed(lines[-limit * 2:]):
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(entries) >= limit:
            break
    return entries


# ------------------------------------------------------------------- budgets

def budget_overview(snapshot: dict) -> dict[str, Any]:
    """Where the money is allocated versus what it is producing.

    Deliberately reports allocation and outcome side by side rather than a
    single 'efficiency' score: the decision is always a comparison, and a
    composite number would hide which half moved.
    """
    currency = snapshot.get("currency", "USD")
    account_spend = snapshot["accountMetrics"].get("spend", 0) or 0

    rows = []
    for campaign in snapshot["campaigns"]:
        metrics = campaign["metrics"]
        daily = _minor(campaign.get("daily_budget")) / 100
        lifetime = _minor(campaign.get("lifetime_budget")) / 100
        rows.append({
            "id": campaign["id"],
            "name": campaign.get("name"),
            "level": "campaign",
            "status": campaign.get("status"),
            "effectiveStatus": campaign.get("effective_status"),
            "objective": campaign.get("objective"),
            "dailyBudget": round(daily, 2) or None,
            "lifetimeBudget": round(lifetime, 2) or None,
            "spend": round(metrics.get("spend", 0), 2),
            "results": metrics.get("results", 0),
            "costPerResult": round(metrics.get("costPerResult", 0), 2),
            "shareOfSpend": round((metrics.get("spend", 0) / account_spend * 100), 1)
            if account_spend else 0,
            "adsets": [
                {
                    "id": adset["id"],
                    "name": adset.get("name"),
                    "level": "adset",
                    "status": adset.get("status"),
                    "dailyBudget": round(_minor(adset.get("daily_budget")) / 100, 2) or None,
                    "spend": round(adset["metrics"].get("spend", 0), 2),
                    "results": adset["metrics"].get("results", 0),
                    "costPerResult": round(adset["metrics"].get("costPerResult", 0), 2),
                    "learningStage": (adset.get("learning_stage_info") or {}).get("status"),
                }
                for adset in snapshot["adsets"]
                if adset.get("campaign_id") == campaign["id"]
            ],
        })

    rows.sort(key=lambda r: -r["spend"])

    active_daily = sum(
        r["dailyBudget"] or 0 for r in rows if r["status"] == "ACTIVE"
    )
    return {
        "currency": currency,
        "accountSpend": round(account_spend, 2),
        "activeDailyBudget": round(active_daily, 2),
        "guardrails": {
            "maxIncreaseMultiple": MAX_BUDGET_MULTIPLE,
            "minDailyBudget": MIN_DAILY_BUDGET_MAJOR,
        },
        "campaigns": rows,
    }
