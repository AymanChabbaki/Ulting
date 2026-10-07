"""
Runs the rule set over a collected snapshot and scores the result.

A rule that raises must not take the audit down with it: Meta's field shapes
vary by account age and objective, and one rule hitting an unexpected None is
not a reason to lose the other twenty findings. Failures are collected and
reported alongside the findings rather than swallowed.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .rules import _LANG, ALL_CATEGORIES, RULES, SEVERITY_ORDER, SEVERITY_WEIGHT


def _score_from(findings: list[dict]) -> int:
    """Score out of 100.

    Penalty per rule is damped with a square root: ten Learning Limited ad sets
    is a worse problem than one, but not ten times worse -- it is one mistake
    repeated. Without damping a single noisy rule drives every account to zero
    and the score stops discriminating between accounts.
    """
    by_rule: dict[str, list[dict]] = {}
    for f in findings:
        by_rule.setdefault(f["ruleId"], []).append(f)

    penalty = 0.0
    for group in by_rule.values():
        weight = SEVERITY_WEIGHT.get(group[0]["severity"], 0)
        penalty += weight * math.sqrt(len(group))

    return max(0, min(100, round(100 - penalty)))


def _grade(score: int) -> dict[str, str]:
    if score >= 90:
        return {"letter": "A", "label": "Healthy"}
    if score >= 75:
        return {"letter": "B", "label": "Minor issues"}
    if score >= 60:
        return {"letter": "C", "label": "Needs attention"}
    if score >= 40:
        return {"letter": "D", "label": "Significant problems"}
    return {"letter": "F", "label": "Critical"}


def run_audit(snapshot: dict, lang: str = "en") -> dict[str, Any]:
    """`lang` ("en" or "fr") is the language findings are written in. Scores,
    ids, categories and evidence are identical in both."""
    findings: list[dict] = []
    rule_errors: list[dict] = []

    token = _LANG.set("fr" if lang == "fr" else "en")
    try:
        for rule in RULES:
            try:
                for result in rule.run(snapshot) or []:
                    findings.append({
                        "ruleId": rule.id,
                        "title": rule.title_for(lang),
                        "category": rule.category,
                        "severity": rule.severity,
                        **result,
                    })
            except Exception as cause:  # noqa: BLE001 - one bad rule must not kill the audit
                rule_errors.append({"ruleId": rule.id, "message": f"{type(cause).__name__}: {cause}"})
    finally:
        _LANG.reset(token)

    findings.sort(key=lambda f: (SEVERITY_ORDER.index(f["severity"]), -f.get("impact", 0)))

    score = _score_from(findings)
    counts = {sev: sum(1 for f in findings if f["severity"] == sev) for sev in SEVERITY_ORDER}

    categories = {}
    for category in ALL_CATEGORIES:
        in_category = [f for f in findings if f["category"] == category]
        categories[category] = {
            "score": _score_from(in_category),
            "findings": len(in_category),
            "critical": sum(1 for f in in_category if f["severity"] == "critical"),
        }

    return {
        "score": score,
        "grade": _grade(score),
        "counts": counts,
        # Money the findings suggest is recoverable. Estimated only by rules
        # that can defend a number (wasted spend, cost above median); rules that
        # cannot leave impact at 0 rather than inventing a figure.
        "estimatedWaste": round(sum(f.get("impact", 0) for f in findings), 2),
        "categories": categories,
        "findings": findings,
        "ruleErrors": rule_errors,
        "rulesRun": len(RULES),
        "auditedAt": datetime.now(timezone.utc).isoformat(),
    }


def summarise(snapshot: dict, audit: dict) -> dict[str, Any]:
    """Compact shape for overview cards and the account list."""
    account = snapshot["account"]

    def live(entities: list[dict]) -> int:
        return sum(1 for e in entities if e.get("status") == "ACTIVE")

    return {
        "accountId": account.get("id"),
        "accountName": account.get("name"),
        "currency": snapshot["currency"],
        "datePreset": snapshot["datePreset"],
        "score": audit["score"],
        "grade": audit["grade"],
        "estimatedWaste": audit["estimatedWaste"],
        "counts": audit["counts"],
        "metrics": snapshot["accountMetrics"],
        "previousMetrics": snapshot.get("previousMetrics") or {},
        "structure": {
            "campaigns": len(snapshot["campaigns"]),
            "activeCampaigns": live(snapshot["campaigns"]),
            "adsets": len(snapshot["adsets"]),
            "activeAdsets": live(snapshot["adsets"]),
            "ads": len(snapshot["ads"]),
            "activeAds": live(snapshot["ads"]),
        },
    }
