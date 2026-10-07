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
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ..meta.metrics import median

# The language findings are written in. Set per audit run (engine.run_audit),
# held in a ContextVar so concurrent requests in different languages never
# see each other's setting.
_LANG: ContextVar[str] = ContextVar("audit_lang", default="en")


def tx(en: str, fr: str) -> str:
    """Pick the text for the current audit language."""
    return fr if _LANG.get() == "fr" else en


def n(value: float, decimals: int = 2) -> str:
    """A number formatted for the current language: 1,234.50 or 1 234,50."""
    text = f"{float(value or 0):,.{decimals}f}"
    if _LANG.get() == "fr":
        # No-break space as the thousands separator: it is in the PDF's
        # built-in font, where the narrow one would print as "?".
        text = text.replace(",", "\u00a0").replace(".", ",")
    return text


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

ACCOUNT_STATUS_FR = {
    "Active": "Actif", "Disabled": "Désactivé", "Unsettled": "Impayé",
    "Pending risk review": "Vérification des risques en cours", "Pending settlement": "Règlement en attente",
    "In grace period": "Période de grâce", "Pending closure": "Fermeture en cours", "Closed": "Fermé",
}

RESULT_LABELS_FR = {
    "Leads": "Leads", "Purchases": "Achats", "Landing page views": "Vues de page de destination",
    "Conversations": "Conversations", "Reach": "Couverture", "App installs": "Installations d'app",
    "Conversions": "Conversions", "Link clicks": "Clics sur lien", "Engagements": "Interactions",
    "Results": "Résultats",
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
    title_fr: str = ""

    def title_for(self, lang: str) -> str:
        return self.title_fr if lang == "fr" and self.title_fr else self.title


# ------------------------------------------------------------------ account

def _account_status(s: dict) -> list[dict]:
    account = s["account"]
    label, ok = ACCOUNT_STATUS.get(account.get("account_status"), (None, True))
    if label is None or ok:
        return []
    shown = tx(label, ACCOUNT_STATUS_FR.get(label, label))
    code = account.get("account_status")
    fix = (
        tx("Settle the outstanding balance in Payment Settings. Unsettled accounts stop "
           "delivery even with active campaigns.",
           "Réglez le solde dû dans les paramètres de paiement. Un compte impayé arrête la "
           "diffusion même avec des campagnes actives.")
        if code == 3
        else tx("Open Business Manager > Billing and resolve the account status before restarting campaigns.",
                "Ouvrez Business Manager > Facturation et régularisez le statut du compte avant de relancer les campagnes.")
    )
    return [finding(
        "account", account["id"], account.get("name", ""),
        tx(f"Account status is {label} (code {code}). Ads will not deliver until this is resolved.",
           f"Le statut du compte est « {shown} » (code {code}). Les publicités ne seront pas "
           f"diffusées tant que ce n'est pas réglé."),
        fix,
        {"account_status": code, "label": label, "disable_reason": account.get("disable_reason")},
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
        tx(f"Balance of {balance:.2f} {currency} covers about {runway:.1f} days at the "
           f"current active daily budget of {daily:.2f} {currency}.",
           f"Le solde de {n(balance)} {currency} couvre environ {n(runway, 1)} jours au budget "
           f"quotidien actif actuel de {n(daily)} {currency}."),
        tx("Top up or raise the payment threshold before delivery pauses mid-flight. "
           "Restarts reset the learning phase.",
           "Rechargez ou relevez le seuil de paiement avant que la diffusion ne s'arrête en "
           "pleine campagne. Un redémarrage relance la phase d'apprentissage."),
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
                tx(f"Set to ACTIVE but effective status is {e['effective_status']}{suffix}. "
                   "It is live in the UI and delivering nothing.",
                   f"Réglé sur ACTIF mais le statut effectif est {e['effective_status']}{suffix}. "
                   "Il apparaît actif dans l'interface et ne diffuse rien."),
                tx("Open the entity in Ads Manager, fix the flagged policy or setup issue, "
                   "and request review.",
                   "Ouvrez l'élément dans le Gestionnaire de publicités, corrigez le problème de "
                   "règle ou de configuration signalé, puis demandez un nouvel examen."),
                {"effective_status": e.get("effective_status"), "issues": issues},
            ))
    return out


def _active_no_delivery(s: dict) -> list[dict]:
    return [
        finding(
            "adset", a["id"], a.get("name", ""),
            tx("Active ad set with zero impressions in the reporting window. Usually a bid cap "
               "below the auction floor, an audience too narrow to fill, or a schedule that "
               "has not started.",
               "Ensemble de publicités actif sans aucune impression sur la période. En général : "
               "une enchère plafonnée sous le prix plancher, une audience trop étroite, ou une "
               "programmation qui n'a pas encore commencé."),
            tx("Check the ad set for a bid/cost cap that is too low, an audience under ~100k, "
               "or an end date already passed.",
               "Vérifiez dans l'ensemble un plafond d'enchère ou de coût trop bas, une audience "
               "sous ~100 000 personnes, ou une date de fin déjà passée."),
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
        events = info.get("conversions", 0)
        out.append(finding(
            "adset", a["id"], a.get("name", ""),
            tx(f"Learning Limited: fewer than 50 optimisation events in 7 days "
               f"({events} recorded). Meta cannot optimise delivery, so "
               "cost per result stays volatile and inflated.",
               f"Apprentissage limité : moins de 50 événements d'optimisation en 7 jours "
               f"({events} enregistrés). Meta ne peut pas optimiser la diffusion, donc le coût "
               "par résultat reste instable et élevé."),
            tx("Consolidate this ad set into a sibling, raise its budget, or move optimisation "
               "to an earlier event (lead instead of purchase) so it clears 50 events/week.",
               "Fusionnez cet ensemble avec un ensemble voisin, augmentez son budget, ou "
               "optimisez sur un événement plus en amont (lead plutôt qu'achat) pour dépasser "
               "50 événements par semaine."),
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
        cur = s["currency"]
        out.append(finding(
            "campaign", c["id"], c.get("name", ""),
            tx(f"{len(children)} active ad sets, {len(thin)} of them under 10 {cur}/day. "
               "Each needs 50 events a week on its own; split this thin, none of them will get there.",
               f"{len(children)} ensembles actifs, dont {len(thin)} à moins de 10 {cur}/jour. "
               "Chacun a besoin de 50 événements par semaine ; avec un budget aussi dispersé, "
               "aucun n'y arrivera."),
            tx("Merge the thin ad sets into one or two, or switch the campaign to Advantage+ "
               "campaign budget so Meta reallocates across them.",
               "Regroupez ces petits ensembles en un ou deux, ou passez la campagne en budget "
               "de campagne Advantage+ pour que Meta répartisse lui-même."),
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
                tx(f"{len(group)} active ad sets share identical targeting ({names}). They bid "
                   "against each other in the same auction, which raises your own CPM.",
                   f"{len(group)} ensembles actifs ont exactement le même ciblage ({names}). Ils "
                   "enchérissent les uns contre les autres dans la même enchère, ce qui fait "
                   "monter votre propre CPM."),
                tx("Keep the best performer and pause the duplicates, or differentiate the "
                   "targeting so they stop competing.",
                   "Gardez le plus performant et mettez les doublons en pause, ou différenciez "
                   "les ciblages pour qu'ils ne se concurrencent plus."),
                {"adsets": [{"id": a["id"], "name": a.get("name"), "spend": a["metrics"]["spend"]}
                            for a in group]},
                impact=sum(a["metrics"]["spend"] * 0.1 for a in group[1:]),
            ))
    return out


def _empty_campaigns(s: dict) -> list[dict]:
    return [
        finding(
            "campaign", c["id"], c.get("name", ""),
            tx("Campaign is active but has no active ad sets. It clutters reporting and hides "
               "which campaigns are genuinely running.",
               "La campagne est active mais n'a aucun ensemble actif. Elle encombre les rapports "
               "et masque les campagnes qui tournent vraiment."),
            tx("Pause the campaign or activate an ad set under it.",
               "Mettez la campagne en pause ou activez un de ses ensembles."),
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
            tx(f'{len(g)} campaigns are named "{g[0].get("name")}". Reporting and any automated '
               "rule keyed on name will silently mix them.",
               f"{len(g)} campagnes s'appellent « {g[0].get('name')} ». Les rapports et toute "
               "règle automatique basée sur le nom les mélangeront sans prévenir."),
            tx("Adopt a naming convention that encodes objective, audience and date, then "
               "rename the duplicates.",
               "Adoptez une convention de nommage (objectif, audience, date), puis renommez les "
               "doublons."),
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
        "account", "account", tx("Account structure", "Structure du compte"),
        tx(f"{len(stale)} campaigns have been paused for over 90 days. Not harmful, but they "
           "make the account hard to read and slow every Ads Manager load.",
           f"{len(stale)} campagnes sont en pause depuis plus de 90 jours. Sans danger, mais "
           "elles rendent le compte difficile à lire et ralentissent le Gestionnaire de publicités."),
        tx("Archive them. Archived campaigns keep their history and stay out of the default view.",
           "Archivez-les. Les campagnes archivées gardent leur historique et sortent de la vue par défaut."),
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
        cur = s["currency"]
        label_en = m["resultLabel"].lower()
        label_fr = RESULT_LABELS_FR.get(m["resultLabel"], m["resultLabel"]).lower()
        out.append(finding(
            "campaign", c["id"], c.get("name", ""),
            tx(f"Spent {m['spend']:.2f} {cur} in the window and recorded zero "
               f"{label_en}. Either the campaign genuinely is not converting, "
               "or the conversion event is not being tracked.",
               f"{n(m['spend'])} {cur} dépensés sur la période sans aucun résultat "
               f"({label_fr}). Soit la campagne ne convertit vraiment pas, soit l'événement de "
               "conversion n'est pas suivi."),
            tx(f"Confirm the {m['resultActionType']} event is firing (Events Manager > Test "
               "Events). If tracking is fine, pause the campaign -- this is pure loss.",
               f"Vérifiez que l'événement {m['resultActionType']} se déclenche (Gestionnaire "
               "d'événements > Tester les événements). Si le suivi fonctionne, mettez la "
               "campagne en pause : c'est une perte sèche."),
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
    cur = s["currency"]
    return [finding(
        "campaign", top["id"], top.get("name", ""),
        tx(f"This campaign carries {share:.0f}% of account spend "
           f"({top['metrics']['spend']:.2f} of {total:.2f} {cur}). Single point of "
           "failure: if it fatigues or gets rejected, the account stops producing.",
           f"Cette campagne porte {n(share, 0)} % des dépenses du compte "
           f"({n(top['metrics']['spend'])} sur {n(total)} {cur}). Point de défaillance unique : "
           "si elle s'essouffle ou est refusée, le compte ne produit plus rien."),
        tx("Stand up a second proven campaign with different creative or audience before this "
           "one declines.",
           "Mettez en place une deuxième campagne éprouvée, avec d'autres créatifs ou une autre "
           "audience, avant que celle-ci ne décline."),
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
    cur = s["currency"]
    for c in converting:
        m = c["metrics"]
        if m["costPerResult"] <= benchmark * 2 or m["spend"] < 20:
            continue
        multiple = m["costPerResult"] / benchmark
        wasted = max(0.0, m["spend"] - m["results"] * benchmark)
        could = int(m["spend"] / benchmark)
        got = int(m["results"])
        out.append(finding(
            "campaign", c["id"], c.get("name", ""),
            tx(f"Cost per result is {m['costPerResult']:.2f} {cur} against an account "
               f"median of {benchmark:.2f} -- {multiple:.1f}x worse.",
               f"Le coût par résultat est de {n(m['costPerResult'])} {cur} contre une médiane "
               f"de compte à {n(benchmark)} — {n(multiple, 1)} fois plus cher."),
            tx(f"Shift this budget to campaigns at or below the median. At median cost the same "
               f"spend would have produced about {could} results instead of {got}.",
               f"Transférez ce budget vers les campagnes au niveau de la médiane ou en dessous. "
               f"Au coût médian, la même dépense aurait produit environ {could} résultats au "
               f"lieu de {got}."),
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
            tx(f"Frequency of {m['frequency']:.2f} -- the same people have seen these ads "
               f"{m['frequency']:.1f} times. Past 3, CTR falls and CPM rises as negative "
               "feedback accumulates.",
               f"Fréquence de {n(m['frequency'])} — les mêmes personnes ont vu ces pubs "
               f"{n(m['frequency'], 1)} fois. Au-delà de 3, le CTR baisse et le CPM monte à "
               "mesure que les retours négatifs s'accumulent."),
            tx("Refresh the creative or widen the audience. Excluding recent converters and "
               "site visitors also pulls frequency down without new assets.",
               "Renouvelez les créatifs ou élargissez l'audience. Exclure les personnes déjà "
               "converties et les visiteurs récents du site fait aussi baisser la fréquence sans "
               "nouveaux visuels."),
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
            tx(f"CTR of {m['ctr']:.2f}% over {int(m['impressions']):,} impressions. Meta prices "
               "low-engagement ads higher, so this compounds into CPM.",
               f"CTR de {n(m['ctr'])} % sur {n(m['impressions'], 0)} impressions. Meta facture "
               "plus cher les pubs qui engagent peu, ce qui se répercute sur le CPM."),
            tx("Test a new hook in the first three seconds and a sharper primary text. Creative, "
               "not bidding, is what moves CTR.",
               "Testez une nouvelle accroche dans les trois premières secondes et un texte "
               "principal plus percutant. C'est le créatif, pas l'enchère, qui fait bouger le CTR."),
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
            tx(f"Only {m['lpvRate']:.0f}% of link clicks became landing page views "
               f"({int(m['landingPageViews']):,} of {int(m['linkClicks']):,}). You are paying for "
               f"{int(lost):,} clicks that never loaded the page.",
               f"Seulement {n(m['lpvRate'], 0)} % des clics sur lien deviennent des vues de page "
               f"de destination ({n(m['landingPageViews'], 0)} sur {n(m['linkClicks'], 0)}). "
               f"Vous payez {n(lost, 0)} clics qui n'ont jamais chargé la page."),
            tx("Page load time is the usual cause -- test the destination on 4G, and confirm the "
               "pixel PageView fires on arrival rather than after a consent banner.",
               "La cause habituelle est le temps de chargement — testez la page en 4G et "
               "vérifiez que le PageView du pixel se déclenche dès l'arrivée, et non après une "
               "bannière de consentement."),
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
            tx("Only one active ad. Meta's delivery system has nothing to choose between, and "
               "there is no read on which creative angle works.",
               "Une seule publicité active. Le système de diffusion de Meta n'a rien à comparer, "
               "et vous ne savez pas quel angle créatif fonctionne."),
            tx("Run 3-4 distinct creatives per ad set so the algorithm can find the winner and "
               "fatigue sets in more slowly.",
               "Faites tourner 3 à 4 créatifs différents par ensemble pour que l'algorithme "
               "trouve le gagnant et que la lassitude arrive plus lentement."),
            {"spend": a["metrics"]["spend"]},
        ))
    return out


def _starved_ads(s: dict) -> list[dict]:
    return [
        finding(
            "ad", a["id"], a.get("name", ""),
            tx(f"{int(a['metrics']['impressions'])} impressions -- below the ~500 Meta needs "
               "before quality rankings appear. This creative has not actually been tested.",
               f"{int(a['metrics']['impressions'])} impressions — sous les ~500 dont Meta a "
               "besoin pour afficher les classements de qualité. Ce créatif n'a pas vraiment été testé."),
            tx("Either give it its own ad set with enough budget to get a real read, or pause it "
               "and stop diluting the ad set.",
               "Donnez-lui son propre ensemble avec assez de budget pour une vraie lecture, ou "
               "mettez-le en pause pour ne plus diluer l'ensemble."),
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
        q = ins.get("quality_ranking", "n/a")
        e = ins.get("engagement_rate_ranking", "n/a")
        cv = ins.get("conversion_rate_ranking", "n/a")
        out.append(finding(
            "ad", a["id"], a.get("name", ""),
            tx(f"Meta rates this ad quality: {q}, engagement: {e}, conversion: {cv}. "
               "Below-average rankings are charged a higher CPM for the same placement.",
               f"Meta note cette pub — qualité : {q}, engagement : {e}, conversion : {cv}. "
               "Un classement sous la moyenne est facturé plus cher en CPM pour le même placement."),
            tx("Replace the creative rather than adjusting budget -- rankings are a creative "
               "signal and do not recover from bid changes.",
               "Remplacez le créatif plutôt que d'ajuster le budget — les classements dépendent "
               "du créatif et ne remontent pas avec un changement d'enchère."),
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
        goal = a.get("optimization_goal")
        out.append(finding(
            "adset", a["id"], a.get("name", ""),
            tx(f"Optimising for {goal or 'a conversion'} but no pixel, page "
               "or app is attached as the promoted object. Meta has no conversion signal to "
               "optimise against.",
               f"Optimisé pour {goal or 'une conversion'} mais aucun pixel, page ou app n'est "
               "rattaché comme objet promu. Meta n'a aucun signal de conversion sur lequel optimiser."),
            tx("Attach the pixel and conversion event in the ad set's Conversion section, then "
               "let it re-enter learning.",
               "Rattachez le pixel et l'événement de conversion dans la section Conversion de "
               "l'ensemble, puis laissez-le repasser en apprentissage."),
            {"optimization_goal": goal, "promoted_object": promoted or None},
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
        "account", "account", tx("Attribution settings", "Paramètres d'attribution"),
        tx(f"Active ad sets run {len(groups)} different attribution windows. Cost per result is "
           "not comparable across them -- a 7-day-click ad set will always look better than a "
           "1-day-click one for the same real performance.",
           f"Les ensembles actifs utilisent {len(groups)} fenêtres d'attribution différentes. Le "
           "coût par résultat n'est donc pas comparable — un ensemble en clic 7 jours paraîtra "
           "toujours meilleur qu'un ensemble en clic 1 jour pour la même performance réelle."),
        tx("Standardise on one window (7-day click / 1-day view is the common default) before "
           "comparing ad sets on cost per result.",
           "Harmonisez sur une seule fenêtre (clic 7 jours / vue 1 jour est le standard) avant "
           "de comparer les ensembles sur le coût par résultat."),
        {"windows": [{"spec": k, "adsets": v} for k, v in groups.items()]},
    )]


RULES: list[Rule] = [
    Rule("account-status", "Ad account cannot spend", Category.ACCOUNT, Severity.CRITICAL, _account_status,
         "Le compte publicitaire ne peut pas dépenser"),
    Rule("account-balance-runway", "Balance runs out within days at current spend", Category.ACCOUNT, Severity.HIGH, _balance_runway,
         "Le solde sera épuisé dans quelques jours au rythme actuel"),
    Rule("delivery-blocked", "Entities rejected or blocked from delivering", Category.DELIVERY, Severity.CRITICAL, _delivery_blocked,
         "Éléments refusés ou bloqués à la diffusion"),
    Rule("active-no-delivery", "Active but getting no impressions", Category.DELIVERY, Severity.HIGH, _active_no_delivery,
         "Actif mais sans aucune impression"),
    Rule("learning-limited", "Ad sets stuck in Learning Limited", Category.DELIVERY, Severity.HIGH, _learning_limited,
         "Ensembles bloqués en apprentissage limité"),
    Rule("fragmented-adsets", "Budget split across too many small ad sets", Category.STRUCTURE, Severity.MEDIUM, _fragmented_adsets,
         "Budget éparpillé sur trop de petits ensembles"),
    Rule("audience-overlap-risk", "Ad sets in one campaign target the same audience", Category.STRUCTURE, Severity.MEDIUM, _audience_overlap,
         "Des ensembles d'une même campagne ciblent la même audience"),
    Rule("empty-campaigns", "Active campaigns with nothing live underneath", Category.STRUCTURE, Severity.LOW, _empty_campaigns,
         "Campagnes actives sans rien d'actif dessous"),
    Rule("duplicate-names", "Campaigns sharing a name", Category.STRUCTURE, Severity.LOW, _duplicate_names,
         "Campagnes portant le même nom"),
    Rule("stale-campaigns", "Long-paused campaigns still in the account", Category.STRUCTURE, Severity.INFO, _stale_campaigns,
         "Campagnes en pause depuis longtemps encore présentes"),
    Rule("spend-no-results", "Spend with zero results", Category.BUDGET, Severity.CRITICAL, _spend_no_results,
         "Dépenses sans aucun résultat"),
    Rule("budget-concentration", "Most of the budget sits in one campaign", Category.BUDGET, Severity.MEDIUM, _budget_concentration,
         "L'essentiel du budget repose sur une seule campagne"),
    Rule("cost-per-result-outlier", "Campaigns far above the account cost per result", Category.PERFORMANCE, Severity.HIGH, _cost_outlier,
         "Campagnes bien au-dessus du coût par résultat du compte"),
    Rule("creative-fatigue", "Frequency high enough to be burning the audience", Category.PERFORMANCE, Severity.HIGH, _creative_fatigue,
         "Fréquence assez élevée pour épuiser l'audience"),
    Rule("landing-page-leak", "Clicks not arriving as landing page views", Category.PERFORMANCE, Severity.HIGH, _landing_page_leak,
         "Des clics qui n'arrivent pas jusqu'à la page"),
    Rule("low-ctr", "Click-through rate below the point where CPM punishes you", Category.PERFORMANCE, Severity.MEDIUM, _low_ctr,
         "Taux de clic assez bas pour faire monter le CPM"),
    Rule("single-ad-adsets", "Ad sets running a single ad", Category.CREATIVE, Severity.MEDIUM, _single_ad_adsets,
         "Ensembles qui ne diffusent qu'une seule pub"),
    Rule("poor-quality-ranking", "Ads ranked below average by Meta", Category.CREATIVE, Severity.MEDIUM, _poor_ranking,
         "Pubs classées sous la moyenne par Meta"),
    Rule("starved-ads", "Ads never given enough impressions to judge", Category.CREATIVE, Severity.LOW, _starved_ads,
         "Pubs qui n'ont jamais eu assez d'impressions pour être jugées"),
    Rule("no-pixel-object", "Conversion campaigns with no promoted object", Category.TRACKING, Severity.HIGH, _no_promoted_object,
         "Campagnes de conversion sans objet promu"),
    Rule("attribution-window", "Ad sets on non-standard attribution windows", Category.TRACKING, Severity.LOW, _attribution_windows,
         "Ensembles sur des fenêtres d'attribution différentes"),
]
