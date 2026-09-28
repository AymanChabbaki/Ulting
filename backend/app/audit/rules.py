"""
The audit rule set.

Each rule is a pure function of the collected snapshot: it returns findings,
never mutates, and never calls the API. That keeps rules testable against a
saved JSON snapshot, and makes a new rule one entry in RULES rather than a
change anywhere else.

A finding is only worth raising if it names the entity, shows the numbers it
fired on, and says what to do. A report full of "CTR is low" with no campaign
attached is what makes people stop reading audits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ..meta.metrics import median


class Severity:
    CRITICAL = "critical"  # money burning or delivery stopped, right now
    HIGH = "high"          # materially costing results this week
    MEDIUM = "medium"      # structural drag, fix this sprint
    LOW = "low"            # hygiene
    INFO = "info"          # context, no action implied


SEVERITY_WEIGHT = {"critical": 25, "high": 12, "medium": 5, "low": 2, "info": 0}
SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]


class Category:
    ACCOUNT = "Account health"
    DELIVERY = "Delivery"
    STRUCTURE = "Structure"
    BUDGET = "Budget & pacing"
    PERFORMANCE = "Performance"
    CREATIVE = "Creative"
    TRACKING = "Tracking"


ALL_CATEGORIES = [
    Category.ACCOUNT, Category.DELIVERY, Category.STRUCTURE,
    Category.BUDGET, Category.PERFORMANCE, Category.CREATIVE, Category.TRACKING,
]

# Meta's numeric account_status, with the ones that stop delivery called out.
ACCOUNT_STATUS = {
    1: ("Active", True),
    2: ("Disabled", False),
    3: ("Unsettled", False),
    7: ("Pending risk review", False),
    8: ("Pending settlement", False),
    9: ("In grace period", False),
    100: ("Pending closure", False),
    101: ("Closed", False),
}


def is_active(entity: dict) -> bool:
    return entity.get("status") == "ACTIVE"


def minor(value: Any) -> float:
    """Meta returns budgets and spend in minor units (cents/fils), never display currency."""
    try:
        return float(value or 0) / 100
    except (TypeError, ValueError):
        return 0.0


def days_since(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).days


def finding(
    entity_level: str,
    entity_id: str,
    entity_name: str,
    detail: str,
    recommendation: str,
    evidence: dict | None = None,
    impact: float = 0.0,
) -> dict:
    return {
        "entity": {"level": entity_level, "id": entity_id, "name": entity_name},
        "detail": detail,
        "recommendation": recommendation,
        "evidence": evidence or {},
        "impact": round(impact, 2),
    }


@dataclass
class Rule:
    id: str
    title: str
    category: str
    severity: str
    run: Callable[[dict], list[dict]]


# ------------------------------------------------------------------ account

def _account_status(s: dict) -> list[dict]:
    account = s["account"]
    label, ok = ACCOUNT_STATUS.get(account.get("account_status"), (None, True))
    if label is None or ok:
        return []
    fix = (
        "Settle the outstanding balance in Payment Settings. Unsettled accounts stop "
        "delivery even with active campaigns."
        if account.get("account_status") == 3
        else "Open Business Manager > Billing and resolve the account status before restarting campaigns."
    )
    return [finding(
        "account", account["id"], account.get("name", ""),
        f"Account status is {label} (code {account.get('account_status')}). "
        f"Ads will not deliver until this is resolved.",
        fix,
        {"account_status": account.get("account_status"), "label": label,
         "disable_reason": account.get("disable_reason")},
    )]


def _balance_runway(s: dict) -> list[dict]:
    account, currency = s["account"], s["currency"]
    balance = minor(account.get("balance"))
    if balance <= 0:
        return []
    daily = sum(minor(c.get("daily_budget")) for c in s["campaigns"] if is_active(c))
    if daily <= 0:
        return []
    runway = balance / daily
    if runway > 7:
        return []
    return [finding(
        "account", account["id"], account.get("name", ""),
        f"Balance of {balance:.2f} {currency} covers about {runway:.1f} days at the "
        f"current active daily budget of {daily:.2f} {currency}.",
        "Top up or raise the payment threshold before delivery pauses mid-flight. "
        "Restarts reset the learning phase.",
        {"balance": balance, "dailyBudget": daily, "runwayDays": round(runway, 1)},
    )]


# ----------------------------------------------------------------- delivery

BLOCKED_STATUSES = {"DISAPPROVED", "WITH_ISSUES", "AD_ACCOUNT_DISABLED", "ADSET_PAUSED_BY_POLICY"}


def _delivery_blocked(s: dict) -> list[dict]:
    out = []
    for level, entities in (("campaign", s["campaigns"]), ("adset", s["adsets"]), ("ad", s["ads"])):
        for e in entities:
            if e.get("effective_status") not in BLOCKED_STATUSES:
                continue
            # A paused parent explains the child; only flag what the user set live.
            if not is_active(e):
                continue
            issues = [
                i.get("error_summary") or i.get("error_message")
                for i in (e.get("issues_info") or [])
            ]
            issues = [i for i in issues if i]
            suffix = f": {issues[0]}" if issues else ""
            out.append(finding(
                level, e["id"], e.get("name", ""),
                f"Set to ACTIVE but effective status is {e['effective_status']}{suffix}. "
                "It is live in the UI and delivering nothing.",
                "Open the entity in Ads Manager, fix the flagged policy or setup issue, "
                "and request review.",
                {"effective_status": e.get("effective_status"), "issues": issues},
            ))
    return out


def _active_no_delivery(s: dict) -> list[dict]:
    return [
        finding(
            "adset", a["id"], a.get("name", ""),
            "Active ad set with zero impressions in the reporting window. Usually a bid cap "
            "below the auction floor, an audience too narrow to fill, or a schedule that "
            "has not started.",
            "Check the ad set for a bid/cost cap that is too low, an audience under ~100k, "
            "or an end date already passed.",
            {"bid_amount": a.get("bid_amount"), "bid_strategy": a.get("bid_strategy"),
             "start_time": a.get("start_time")},
        )
        for a in s["adsets"]
        if is_active(a) and a.get("effective_status") == "ACTIVE" and a["metrics"]["impressions"] == 0
    ]


def _learning_limited(s: dict) -> list[dict]:
    out = []
    for a in s["adsets"]:
        info = a.get("learning_stage_info") or {}
        if not is_active(a) or info.get("status") != "LEARNING_LIMITED":
            continue
        out.append(finding(
            "adset", a["id"], a.get("name", ""),
            f"Learning Limited: fewer than 50 optimisation events in 7 days "
            f"({info.get('conversions', 0)} recorded). Meta cannot optimise delivery, so "
            "cost per result stays volatile and inflated.",
            "Consolidate this ad set into a sibling, raise its budget, or move optimisation "
            "to an earlier event (lead instead of purchase) so it clears 50 events/week.",
            {"learning": info, "spend": a["metrics"]["spend"], "results": a["metrics"]["results"]},
            impact=a["metrics"]["spend"] * 0.15,
        ))
    return out


# ---------------------------------------------------------------- structure

def _fragmented_adsets(s: dict) -> list[dict]:
    out = []
    for c in s["campaigns"]:
        if not is_active(c):
            continue
        children = [a for a in s["adsets"] if a.get("campaign_id") == c["id"] and is_active(a)]
        if len(children) < 3:
            continue
        thin = [a for a in children if 0 < minor(a.get("daily_budget")) < 10]
        if len(thin) < 3:
            continue
        out.append(finding(
            "campaign", c["id"], c.get("name", ""),
            f"{len(children)} active ad sets, {len(thin)} of them under 10 {s['currency']}/day. "
            "Each needs 50 events a week on its own; split this thin, none of them will get there.",
            "Merge the thin ad sets into one or two, or switch the campaign to Advantage+ "
            "campaign budget so Meta reallocates across them.",
            {"activeAdsets": len(children), "thinAdsets": len(thin)},
        ))
    return out


def _targeting_signature(adset: dict) -> str:
    """Geo has to go all the way down to cities.

    City-split ad sets are the most common legitimate reason to run several ad
    sets in one campaign, and a signature that stops at `countries` reports
    every one of them as a duplicate -- the fastest way to make an audit
    untrustworthy.
    """
    t = adset.get("targeting") or {}
    geo = t.get("geo_locations") or {}

    def keys(items, key="key"):
        return sorted(str(i.get(key, i)) if isinstance(i, dict) else str(i) for i in (items or []))

    return json.dumps({
        "countries": keys(geo.get("countries")),
        "regions": keys(geo.get("regions")),
        "cities": keys(geo.get("cities")),
        "zips": keys(geo.get("zips")),
        "excludedCities": keys((t.get("excluded_geo_locations") or {}).get("cities")),
        "age": [t.get("age_min"), t.get("age_max")],
        "genders": t.get("genders") or [],
        "interests": sorted(
            str(i.get("id"))
            for spec in (t.get("flexible_spec") or [])
            for i in (spec.get("interests") or [])
        ),
        "customAudiences": keys(t.get("custom_audiences"), "id"),
        "excludedCustomAudiences": keys(t.get("excluded_custom_audiences"), "id"),
    }, sort_keys=True)


def _audience_overlap(s: dict) -> list[dict]:
    out = []
    for c in s["campaigns"]:
        if not is_active(c):
            continue
        children = [a for a in s["adsets"] if a.get("campaign_id") == c["id"] and is_active(a)]
        groups: dict[str, list[dict]] = {}
        for a in children:
            groups.setdefault(_targeting_signature(a), []).append(a)
        for group in groups.values():
            if len(group) < 2:
                continue
            names = ", ".join(a.get("name", "") for a in group)
            out.append(finding(
                "campaign", c["id"], c.get("name", ""),
                f"{len(group)} active ad sets share identical targeting ({names}). They bid "
                "against each other in the same auction, which raises your own CPM.",
                "Keep the best performer and pause the duplicates, or differentiate the "
                "targeting so they stop competing.",
                {"adsets": [{"id": a["id"], "name": a.get("name"), "spend": a["metrics"]["spend"]}
                            for a in group]},
                impact=sum(a["metrics"]["spend"] * 0.1 for a in group[1:]),
            ))
    return out


def _empty_campaigns(s: dict) -> list[dict]:
    return [
        finding(
            "campaign", c["id"], c.get("name", ""),
            "Campaign is active but has no active ad sets. It clutters reporting and hides "
            "which campaigns are genuinely running.",
            "Pause the campaign or activate an ad set under it.",
        )
        for c in s["campaigns"]
        if is_active(c)
        and not any(a.get("campaign_id") == c["id"] and is_active(a) for a in s["adsets"])
    ]


def _duplicate_names(s: dict) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for c in s["campaigns"]:
        groups.setdefault((c.get("name") or "").strip().lower(), []).append(c)
    return [
        finding(
            "campaign", g[0]["id"], g[0].get("name", ""),
            f'{len(g)} campaigns are named "{g[0].get("name")}". Reporting and any automated '
            "rule keyed on name will silently mix them.",
            "Adopt a naming convention that encodes objective, audience and date, then "
            "rename the duplicates.",
            {"ids": [c["id"] for c in g]},
        )
        for g in groups.values()
        if len(g) > 1
    ]


def _stale_campaigns(s: dict) -> list[dict]:
    stale = [
        c for c in s["campaigns"]
        if c.get("status") == "PAUSED" and days_since(c.get("updated_time")) > 90
    ]
    if len(stale) < 5:
        return []
    return [finding(
        "account", "account", "Account structure",
        f"{len(stale)} campaigns have been paused for over 90 days. Not harmful, but they "
        "make the account hard to read and slow every Ads Manager load.",
        "Archive them. Archived campaigns keep their history and stay out of the default view.",
        {"count": len(stale), "examples": [c.get("name") for c in stale[:5]]},
    )]


# ------------------------------------------------------------------- budget

def _spend_no_results(s: dict) -> list[dict]:
    out = []
    for c in s["campaigns"]:
        m = c["metrics"]
        # Below 20 units of spend this is noise, not a finding.
        if m["spend"] < 20 or m["results"] > 0:
            continue
        out.append(finding(
            "campaign", c["id"], c.get("name", ""),
            f"Spent {m['spend']:.2f} {s['currency']} in the window and recorded zero "
            f"{m['resultLabel'].lower()}. Either the campaign genuinely is not converting, "
            "or the conversion event is not being tracked.",
            f"Confirm the {m['resultActionType']} event is firing (Events Manager > Test "
            "Events). If tracking is fine, pause the campaign -- this is pure loss.",
            {"spend": m["spend"], "impressions": m["impressions"], "clicks": m["clicks"],
             "objective": c.get("objective"), "expectedAction": m["resultActionType"]},
            impact=m["spend"],
        ))
    return out


def _budget_concentration(s: dict) -> list[dict]:
    total = s["accountMetrics"]["spend"]
    spenders = [c for c in s["campaigns"] if c["metrics"]["spend"] > 0]
    if total <= 0 or len(spenders) < 3:
        return []
    top = max(spenders, key=lambda c: c["metrics"]["spend"])
    share = top["metrics"]["spend"] / total * 100
    if share < 70:
        return []
    return [finding(
        "campaign", top["id"], top.get("name", ""),
        f"This campaign carries {share:.0f}% of account spend "
        f"({top['metrics']['spend']:.2f} of {total:.2f} {s['currency']}). Single point of "
        "failure: if it fatigues or gets rejected, the account stops producing.",
        "Stand up a second proven campaign with different creative or audience before this "
        "one declines.",
        {"share": round(share, 1), "campaignSpend": top["metrics"]["spend"], "accountSpend": total},
    )]


# -------------------------------------------------------------- performance

def _cost_outlier(s: dict) -> list[dict]:
    converting = [c for c in s["campaigns"] if c["metrics"]["results"] > 0 and c["metrics"]["spend"] > 0]
    if len(converting) < 3:
        return []
    benchmark = median(c["metrics"]["costPerResult"] for c in converting)
    if benchmark <= 0:
        return []
    out = []
    for c in converting:
        m = c["metrics"]
        if m["costPerResult"] <= benchmark * 2 or m["spend"] < 20:
            continue
        multiple = m["costPerResult"] / benchmark
        wasted = max(0.0, m["spend"] - m["results"] * benchmark)
        out.append(finding(
            "campaign", c["id"], c.get("name", ""),
            f"Cost per result is {m['costPerResult']:.2f} {s['currency']} against an account "
            f"median of {benchmark:.2f} -- {multiple:.1f}x worse.",
            f"Shift this budget to campaigns at or below the median. At median cost the same "
            f"spend would have produced about {int(m['spend'] / benchmark)} results instead "
            f"of {int(m['results'])}.",
            {"costPerResult": round(m["costPerResult"], 2), "benchmark": round(benchmark, 2),
             "multiple": round(multiple, 1), "spend": m["spend"], "results": m["results"]},
            impact=wasted,
        ))
    return out


def _creative_fatigue(s: dict) -> list[dict]:
    out = []
    for a in s["adsets"]:
        m = a["metrics"]
        if not is_active(a) or m["frequency"] < 3 or m["impressions"] <= 1000:
            continue
        out.append(finding(
            "adset", a["id"], a.get("name", ""),
            f"Frequency of {m['frequency']:.2f} -- the same people have seen these ads "
            f"{m['frequency']:.1f} times. Past 3, CTR falls and CPM rises as negative "
            "feedback accumulates.",
            "Refresh the creative or widen the audience. Excluding recent converters and "
            "site visitors also pulls frequency down without new assets.",
            {"frequency": round(m["frequency"], 2), "reach": m["reach"],
             "impressions": m["impressions"], "ctr": round(m["ctr"], 2)},
            impact=m["spend"] * 0.2,
        ))
    return out


def _low_ctr(s: dict) -> list[dict]:
    out = []
    for c in s["campaigns"]:
        m = c["metrics"]
        if not is_active(c) or m["impressions"] <= 5000 or m["ctr"] >= 1:
            continue
        out.append(finding(
            "campaign", c["id"], c.get("name", ""),
            f"CTR of {m['ctr']:.2f}% over {int(m['impressions']):,} impressions. Meta prices "
            "low-engagement ads higher, so this compounds into CPM.",
            "Test a new hook in the first three seconds and a sharper primary text. Creative, "
            "not bidding, is what moves CTR.",
            {"ctr": round(m["ctr"], 2), "impressions": m["impressions"], "cpm": round(m["cpm"], 2)},
        ))
    return out


def _landing_page_leak(s: dict) -> list[dict]:
    out = []
    for c in s["campaigns"]:
        m = c["metrics"]
        if m["linkClicks"] < 100 or not (0 < m["lpvRate"] < 60):
            continue
        lost = m["linkClicks"] - m["landingPageViews"]
        out.append(finding(
            "campaign", c["id"], c.get("name", ""),
            f"Only {m['lpvRate']:.0f}% of link clicks became landing page views "
            f"({int(m['landingPageViews']):,} of {int(m['linkClicks']):,}). You are paying for "
            f"{int(lost):,} clicks that never loaded the page.",
            "Page load time is the usual cause -- test the destination on 4G, and confirm the "
            "pixel PageView fires on arrival rather than after a consent banner.",
            {"linkClicks": m["linkClicks"], "landingPageViews": m["landingPageViews"],
             "lpvRate": round(m["lpvRate"], 1), "lostClicks": lost},
            impact=m["cpc"] * lost,
        ))
    return out


# ----------------------------------------------------------------- creative

def _single_ad_adsets(s: dict) -> list[dict]:
    out = []
    for a in s["adsets"]:
        if not is_active(a) or a["metrics"]["spend"] <= 0:
            continue
        live = [ad for ad in s["ads"] if ad.get("adset_id") == a["id"] and is_active(ad)]
        if len(live) != 1:
            continue
        out.append(finding(
            "adset", a["id"], a.get("name", ""),
            "Only one active ad. Meta's delivery system has nothing to choose between, and "
            "there is no read on which creative angle works.",
            "Run 3-4 distinct creatives per ad set so the algorithm can find the winner and "
            "fatigue sets in more slowly.",
            {"spend": a["metrics"]["spend"]},
        ))
    return out


def _starved_ads(s: dict) -> list[dict]:
    return [
        finding(
            "ad", a["id"], a.get("name", ""),
            f"{int(a['metrics']['impressions'])} impressions -- below the ~500 Meta needs "
            "before quality rankings appear. This creative has not actually been tested.",
            "Either give it its own ad set with enough budget to get a real read, or pause it "
            "and stop diluting the ad set.",
            {"impressions": a["metrics"]["impressions"], "spend": a["metrics"]["spend"]},
        )
        for a in s["ads"]
        if is_active(a) and 0 < a["metrics"]["impressions"] < 500
    ]


BAD_RANKINGS = {"BELOW_AVERAGE_10", "BELOW_AVERAGE_20", "BELOW_AVERAGE_35"}


def _poor_ranking(s: dict) -> list[dict]:
    out = []
    for a in s["ads"]:
        if not is_active(a):
            continue
        ins = a.get("insights") or {}
        if ins.get("quality_ranking") not in BAD_RANKINGS and \
           ins.get("engagement_rate_ranking") not in BAD_RANKINGS:
            continue
        out.append(finding(
            "ad", a["id"], a.get("name", ""),
            f"Meta rates this ad quality: {ins.get('quality_ranking', 'n/a')}, engagement: "
            f"{ins.get('engagement_rate_ranking', 'n/a')}, conversion: "
            f"{ins.get('conversion_rate_ranking', 'n/a')}. Below-average rankings are charged "
            "a higher CPM for the same placement.",
            "Replace the creative rather than adjusting budget -- rankings are a creative "
            "signal and do not recover from bid changes.",
            {"quality_ranking": ins.get("quality_ranking"),
             "engagement_rate_ranking": ins.get("engagement_rate_ranking"),
             "conversion_rate_ranking": ins.get("conversion_rate_ranking"),
             "cpm": round(a["metrics"]["cpm"], 2)},
            impact=a["metrics"]["spend"] * 0.15,
        ))
    return out


# ----------------------------------------------------------------- tracking

CONVERSION_OBJECTIVES = {"OUTCOME_SALES", "OUTCOME_LEADS", "CONVERSIONS", "LEAD_GENERATION"}


def _no_promoted_object(s: dict) -> list[dict]:
    out = []
    for a in s["adsets"]:
        if not is_active(a) or a.get("objective") not in CONVERSION_OBJECTIVES:
            continue
        promoted = a.get("promoted_object") or {}
        if promoted.get("pixel_id") or promoted.get("page_id") or promoted.get("application_id"):
            continue
        out.append(finding(
            "adset", a["id"], a.get("name", ""),
            f"Optimising for {a.get('optimization_goal') or 'a conversion'} but no pixel, page "
            "or app is attached as the promoted object. Meta has no conversion signal to "
            "optimise against.",
            "Attach the pixel and conversion event in the ad set's Conversion section, then "
            "let it re-enter learning.",
            {"optimization_goal": a.get("optimization_goal"), "promoted_object": promoted or None},
            impact=a["metrics"]["spend"] * 0.25,
        ))
    return out


def _attribution_windows(s: dict) -> list[dict]:
    groups: dict[str, int] = {}
    for a in s["adsets"]:
        if not is_active(a):
            continue
        spec = json.dumps(a.get("attribution_spec"), sort_keys=True) if a.get("attribution_spec") else "default"
        groups[spec] = groups.get(spec, 0) + 1
    if len(groups) < 2:
        return []
    return [finding(
        "account", "account", "Attribution settings",
        f"Active ad sets run {len(groups)} different attribution windows. Cost per result is "
        "not comparable across them -- a 7-day-click ad set will always look better than a "
        "1-day-click one for the same real performance.",
        "Standardise on one window (7-day click / 1-day view is the common default) before "
        "comparing ad sets on cost per result.",
        {"windows": [{"spec": k, "adsets": v} for k, v in groups.items()]},
    )]


RULES: list[Rule] = [
    Rule("account-status", "Ad account cannot spend", Category.ACCOUNT, Severity.CRITICAL, _account_status),
    Rule("account-balance-runway", "Balance runs out within days at current spend", Category.ACCOUNT, Severity.HIGH, _balance_runway),
    Rule("delivery-blocked", "Entities rejected or blocked from delivering", Category.DELIVERY, Severity.CRITICAL, _delivery_blocked),
    Rule("active-no-delivery", "Active but getting no impressions", Category.DELIVERY, Severity.HIGH, _active_no_delivery),
    Rule("learning-limited", "Ad sets stuck in Learning Limited", Category.DELIVERY, Severity.HIGH, _learning_limited),
    Rule("fragmented-adsets", "Budget split across too many small ad sets", Category.STRUCTURE, Severity.MEDIUM, _fragmented_adsets),
    Rule("audience-overlap-risk", "Ad sets in one campaign target the same audience", Category.STRUCTURE, Severity.MEDIUM, _audience_overlap),
    Rule("empty-campaigns", "Active campaigns with nothing live underneath", Category.STRUCTURE, Severity.LOW, _empty_campaigns),
    Rule("duplicate-names", "Campaigns sharing a name", Category.STRUCTURE, Severity.LOW, _duplicate_names),
    Rule("stale-campaigns", "Long-paused campaigns still in the account", Category.STRUCTURE, Severity.INFO, _stale_campaigns),
    Rule("spend-no-results", "Spend with zero results", Category.BUDGET, Severity.CRITICAL, _spend_no_results),
    Rule("budget-concentration", "Most of the budget sits in one campaign", Category.BUDGET, Severity.MEDIUM, _budget_concentration),
    Rule("cost-per-result-outlier", "Campaigns far above the account cost per result", Category.PERFORMANCE, Severity.HIGH, _cost_outlier),
    Rule("creative-fatigue", "Frequency high enough to be burning the audience", Category.PERFORMANCE, Severity.HIGH, _creative_fatigue),
    Rule("landing-page-leak", "Clicks not arriving as landing page views", Category.PERFORMANCE, Severity.HIGH, _landing_page_leak),
    Rule("low-ctr", "Click-through rate below the point where CPM punishes you", Category.PERFORMANCE, Severity.MEDIUM, _low_ctr),
    Rule("single-ad-adsets", "Ad sets running a single ad", Category.CREATIVE, Severity.MEDIUM, _single_ad_adsets),
    Rule("poor-quality-ranking", "Ads ranked below average by Meta", Category.CREATIVE, Severity.MEDIUM, _poor_ranking),
    Rule("starved-ads", "Ads never given enough impressions to judge", Category.CREATIVE, Severity.LOW, _starved_ads),
    Rule("no-pixel-object", "Conversion campaigns with no promoted object", Category.TRACKING, Severity.HIGH, _no_promoted_object),
    Rule("attribution-window", "Ad sets on non-standard attribution windows", Category.TRACKING, Severity.LOW, _attribution_windows),
]
