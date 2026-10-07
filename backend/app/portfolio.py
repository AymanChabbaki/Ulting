"""
Combining several ad accounts into one view.

The hard constraint is currency. These accounts are a mix of USD and AED, and
adding those together produces a number that means nothing -- so money is never
summed across currencies here. Counts (impressions, clicks, results) are
unit-free and do sum; ratios derived purely from counts (CTR) sum correctly too.
Anything with money in it (CPM, CPC, cost per result, ROAS) is reported per
currency, or omitted when the selection is mixed.

Getting this wrong is the classic portfolio-dashboard bug: a headline "total
spend" that silently adds dirhams to dollars and reads ~4x too high.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .audit.engine import run_audit, summarise
from .meta.collect import collect_account
from .meta.window import Window

# A cap, not a preference: each account is ~10 Graph calls, and the client
# serialises them three at a time. Six is already a slow request on a
# development-access token.
MAX_ACCOUNTS = 6

COUNT_KEYS = ("impressions", "reach", "clicks", "linkClicks", "landingPageViews", "results")


def _blank_combined() -> dict[str, Any]:
    return {key: 0.0 for key in COUNT_KEYS}


def combine(accounts: list[dict]) -> dict[str, Any]:
    """Roll per-account summaries into a currency-safe total."""
    combined = _blank_combined()
    spend_by_currency: dict[str, float] = {}
    revenue_by_currency: dict[str, float] = {}
    results_by_currency: dict[str, float] = {}

    for entry in accounts:
        metrics = entry.get("metrics") or {}
        currency = entry.get("currency") or "USD"

        for key in COUNT_KEYS:
            combined[key] += float(metrics.get(key) or 0)

        spend_by_currency[currency] = spend_by_currency.get(currency, 0.0) + float(
            metrics.get("spend") or 0
        )
        revenue_by_currency[currency] = revenue_by_currency.get(currency, 0.0) + float(
            metrics.get("revenue") or 0
        )
        results_by_currency[currency] = results_by_currency.get(currency, 0.0) + float(
            metrics.get("results") or 0
        )

    currencies = sorted(spend_by_currency)
    mixed = len(currencies) > 1

    impressions = combined["impressions"]
    clicks = combined["clicks"]

    # Cost per result is only meaningful within one currency.
    cost_per_result = {
        cur: (spend / results_by_currency[cur]) if results_by_currency.get(cur) else 0.0
        for cur, spend in spend_by_currency.items()
    }
    cpm = {
        cur: (spend / impressions * 1000) if impressions > 0 else 0.0
        for cur, spend in spend_by_currency.items()
    }

    return {
        **combined,
        # Unit-free ratios are safe to combine.
        "ctr": (clicks / impressions * 100) if impressions > 0 else 0.0,
        "linkCtr": (combined["linkClicks"] / impressions * 100) if impressions > 0 else 0.0,
        "lpvRate": (
            combined["landingPageViews"] / combined["linkClicks"] * 100
            if combined["linkClicks"] > 0
            else 0.0
        ),
        # Reach does not sum across accounts (the same person may be reached by
        # several), so this is an upper bound and labelled as such in the UI.
        "frequency": (impressions / combined["reach"]) if combined["reach"] > 0 else 0.0,
        "spendByCurrency": {c: round(v, 2) for c, v in spend_by_currency.items()},
        "revenueByCurrency": {c: round(v, 2) for c, v in revenue_by_currency.items()},
        "costPerResultByCurrency": {c: round(v, 2) for c, v in cost_per_result.items()},
        "cpmByCurrency": {c: round(v, 4) for c, v in cpm.items()},
        "currencies": currencies,
        "mixedCurrency": mixed,
        # Present only when the selection is single-currency, so the UI can show
        # one headline number without qualifying it.
        "spend": round(sum(spend_by_currency.values()), 2) if not mixed else None,
        "currency": currencies[0] if len(currencies) == 1 else None,
    }


async def build_portfolio(account_ids: list[str], window: Window, lang: str = "en") -> dict[str, Any]:
    """Audit several accounts and merge the results."""
    ids = list(dict.fromkeys(account_ids))[:MAX_ACCOUNTS]

    async def one(account_id: str):
        try:
            snapshot = await collect_account(account_id, window)
        except Exception as cause:  # noqa: BLE001 - one bad account must not sink the view
            return {
                "accountId": account_id,
                "error": getattr(cause, "message", None) or str(cause),
            }
        audit = run_audit(snapshot, lang)
        summary = summarise(snapshot, audit)
        return {
            "accountId": summary["accountId"],
            "accountName": summary["accountName"],
            "currency": summary["currency"],
            "score": summary["score"],
            "grade": summary["grade"],
            "counts": summary["counts"],
            "estimatedWaste": summary["estimatedWaste"],
            "metrics": summary["metrics"],
            "previousMetrics": summary["previousMetrics"],
            "structure": summary["structure"],
            "warnings": snapshot.get("warnings", []),
            # Findings carry their account so a merged list stays attributable.
            "findings": [
                {**f, "account": {"id": summary["accountId"], "name": summary["accountName"]}}
                for f in audit["findings"]
            ],
        }

    results = await asyncio.gather(*(one(i) for i in ids))

    ok = [r for r in results if "error" not in r]
    failed = [r for r in results if "error" in r]

    findings: list[dict] = []
    for entry in ok:
        findings.extend(entry.pop("findings"))

    severity_order = ["critical", "high", "medium", "low", "info"]
    findings.sort(key=lambda f: (severity_order.index(f["severity"]), -f.get("impact", 0)))

    counts = {
        sev: sum(1 for f in findings if f["severity"] == sev) for sev in severity_order
    }

    # Waste is money, so it is grouped by currency like every other amount.
    waste_by_currency: dict[str, float] = {}
    for entry in ok:
        cur = entry["currency"]
        waste_by_currency[cur] = waste_by_currency.get(cur, 0.0) + entry["estimatedWaste"]

    warnings = [
        {**w, "accountId": entry["accountId"]} for entry in ok for w in entry.get("warnings", [])
    ]

    return {
        "accounts": ok,
        "failed": failed,
        "combined": combine(ok),
        "findings": findings,
        "counts": counts,
        "estimatedWasteByCurrency": {c: round(v, 2) for c, v in waste_by_currency.items()},
        # Straight mean, not weighted by spend: this answers "how healthy are my
        # accounts on average", and weighting would let one big account hide
        # several broken small ones.
        "averageScore": round(sum(e["score"] for e in ok) / len(ok)) if ok else 0,
        "warnings": warnings,
        "window": {
            "key": window.key,
            "label": window.label,
            "since": window.since.isoformat() if window.since else None,
            "until": window.until.isoformat() if window.until else None,
        },
    }
