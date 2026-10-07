"""
Designed PDF report of one ad account over one reporting window.

Built with reportlab: pure Python, no system libraries, so it renders the same
on a Windows laptop and in the slim Docker image. The layout is drawn, not
converted from HTML -- cover page with the brand logo and health score,
executive summary with KPI cards and plain-language takeaways, daily trend
charts, campaign and ad tables, and the audit findings with recommendations.

Everything in it comes from the same snapshot and audit the dashboard shows,
so the PDF and the screen never disagree. French or English labels.
"""

from __future__ import annotations

import io
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from .creative import BRAND_DIR

NAVY = colors.HexColor("#0d2a52")
BLUE = colors.HexColor("#0b5cb8")
GOLD = colors.HexColor("#f8c000")
INK = colors.HexColor("#16160f")
MUTED = colors.HexColor("#6b6b66")
LINE = colors.HexColor("#e5e5e1")
SOFT = colors.HexColor("#f6f7f9")
GOOD = colors.HexColor("#12805c")
BAD = colors.HexColor("#b42318")

SEVERITY = {
    "critical": colors.HexColor("#b42318"),
    "high": colors.HexColor("#e8590c"),
    "medium": colors.HexColor("#d4a000"),
    "low": colors.HexColor("#0b5cb8"),
    "info": colors.HexColor("#85847e"),
}

# Metrics where a decrease is the good direction.
LOWER_IS_BETTER = {"costPerResult", "cpm", "cpc", "frequency"}

PAGE_W, PAGE_H = A4
MARGIN = 16 * mm

TEXT = {
    "en": {
        "title": "Meta Ads performance report",
        "period": "Reporting period",
        "generated": "Generated",
        "health": "Account health",
        "summary": "Executive summary",
        "takeaways": "Key takeaways",
        "kpis": "Key figures",
        "vs_prev": "vs previous period",
        "trend": "Daily trend",
        "daily_spend": "Daily spend",
        "daily_results": "Daily results",
        "campaigns": "Campaigns",
        "ads_best": "Best ads (lowest cost per result)",
        "ads_waste": "Ads spending without results",
        "audit": "Audit",
        "categories": "Score by area",
        "findings": "Findings and recommendations",
        "no_findings": "No issues found in this period.",
        "recommendation": "Recommendation",
        "impact": "Estimated recoverable",
        "name": "Name", "status": "Status", "spend": "Spend", "results": "Results",
        "cpr": "Cost / result", "ctr": "CTR", "share": "Share",
        "impressions": "Impressions", "reach": "Reach", "frequency": "Frequency",
        "cpm": "CPM", "clicks": "Link clicks", "cpc": "CPC",
        "structure": "{c} campaigns · {a} ad sets · {d} ads ({ac} campaigns active)",
        "waste_line": "Estimated recoverable spend from the findings: {v}.",
        "t_overview": "Spent {spend} for {results} {label} at {cpr} per result{delta}.",
        "t_best": "Best campaign: {name} — {results} results at {cpr} each.",
        "t_dead": "{name} spent {spend} with no results.",
        "t_audit": "Health score {score}/100 (grade {grade}): {crit} critical and {high} high-priority issues.",
        "t_top": "First action: {text}",
        "delta_fmt": " ({sign}{pct}% vs previous period)",
        "none": "—",
        "page": "Page",
        "confidential": "Confidential — prepared for",
        "sev": {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "info": "Info"},
        "cat": {},
        "statuses": {},
        "levels": {},
    },
    "fr": {
        "title": "Rapport de performance Meta Ads",
        "period": "Période analysée",
        "generated": "Généré le",
        "health": "Santé du compte",
        "summary": "Synthèse",
        "takeaways": "Points clés",
        "kpis": "Chiffres clés",
        "vs_prev": "vs période précédente",
        "trend": "Évolution quotidienne",
        "daily_spend": "Dépenses par jour",
        "daily_results": "Résultats par jour",
        "campaigns": "Campagnes",
        "ads_best": "Meilleures publicités (coût par résultat le plus bas)",
        "ads_waste": "Publicités qui dépensent sans résultat",
        "audit": "Audit",
        "categories": "Score par domaine",
        "findings": "Constats et recommandations",
        "no_findings": "Aucun problème détecté sur cette période.",
        "recommendation": "Recommandation",
        "impact": "Montant récupérable estimé",
        "name": "Nom", "status": "Statut", "spend": "Dépenses", "results": "Résultats",
        "cpr": "Coût / résultat", "ctr": "CTR", "share": "Part",
        "impressions": "Impressions", "reach": "Couverture", "frequency": "Fréquence",
        "cpm": "CPM", "clicks": "Clics sur lien", "cpc": "CPC",
        "structure": "{c} campagnes · {a} ensembles · {d} publicités ({ac} campagnes actives)",
        "waste_line": "Dépenses récupérables estimées d'après les constats : {v}.",
        "t_overview": "{spend} dépensés pour {results} {label}, soit {cpr} par résultat{delta}.",
        "t_best": "Meilleure campagne : {name} — {results} résultats à {cpr} chacun.",
        "t_dead": "{name} a dépensé {spend} sans aucun résultat.",
        "t_audit": "Score de santé {score}/100 (note {grade}) : {crit} problème(s) critique(s) et {high} prioritaire(s).",
        "t_top": "Première action : {text}",
        "delta_fmt": " ({sign}{pct} % vs période précédente)",
        "none": "—",
        "page": "Page",
        "confidential": "Confidentiel — préparé pour",
        "sev": {"critical": "Critique", "high": "Élevé", "medium": "Moyen", "low": "Faible", "info": "Info"},
        "cat": {"Account health": "Santé du compte", "Delivery": "Diffusion", "Structure": "Structure",
                "Budget & pacing": "Budget et rythme", "Performance": "Performance",
                "Creative": "Créatifs", "Tracking": "Suivi / pixel"},
        "levels": {"account": "compte", "campaign": "campagne", "adset": "ensemble", "ad": "publicité"},
        "statuses": {"ACTIVE": "Active", "PAUSED": "En pause", "CAMPAIGN_PAUSED": "Campagne en pause",
                     "ADSET_PAUSED": "Ensemble en pause", "ARCHIVED": "Archivée", "DELETED": "Supprimée",
                     "DISAPPROVED": "Refusée", "PENDING_REVIEW": "En examen", "WITH_ISSUES": "Problème"},
    },
}


# ------------------------------------------------------------------ helpers

def _safe(text: Any) -> str:
    """Escape for Paragraph markup and drop characters the built-in fonts lack
    (Arabic, emoji) rather than printing black boxes."""
    s = "" if text is None else str(text)
    return escape(s.encode("cp1252", "replace").decode("cp1252"))


# Report language, set by build_report, for the number helpers below.
_LANG: ContextVar[str] = ContextVar("report_lang", default="fr")


def _fmt(v: float, decimals: int) -> str:
    """1,234.50 in English, 1 234,50 in French (no-break space: in the font)."""
    text = f"{float(v or 0):,.{decimals}f}"
    if _LANG.get() == "fr":
        text = text.replace(",", "\u00a0").replace(".", ",")
    return text


def _money(v: float, cur: str) -> str:
    v = float(v or 0)
    return f"{_fmt(v, 0 if abs(v) >= 100_000 else 2)} {cur}"


def _num(v: float) -> str:
    v = float(v or 0)
    return _fmt(v, 0) if v >= 100 or v == int(v) else _fmt(v, 1)


def _pct(v: float) -> str:
    return f"{_fmt(v, 2)} %" if _LANG.get() == "fr" else f"{_fmt(v, 2)}%"


GRADE_FR = {"Healthy": "Sain", "Minor issues": "Problèmes mineurs", "Needs attention": "À surveiller",
            "Significant problems": "Problèmes importants", "Critical": "Critique"}


def _grade(audit: dict, lang: str) -> tuple[str, str]:
    """(letter, label) -- the audit's grade is a dict, never print it raw."""
    g = audit.get("grade") or {}
    if not isinstance(g, dict):
        return str(g), ""
    label = g.get("label", "")
    return g.get("letter", ""), (GRADE_FR.get(label, label) if lang == "fr" else label)


def _delta(key: str, cur: dict, prev: dict) -> tuple[str, colors.Color] | None:
    if not prev:
        return None
    a, b = float(cur.get(key) or 0), float(prev.get(key) or 0)
    if b == 0:
        return None
    change = (a - b) / b * 100
    good = change < 0 if key in LOWER_IS_BETTER else change > 0
    if abs(change) < 0.5:
        return "=", MUTED
    # Signs, not arrow glyphs: the built-in PDF fonts have no arrows.
    sign = "+" if change > 0 else "-"
    return f"{sign}{abs(change):.0f}%", (GOOD if good else BAD) if abs(change) >= 0.5 else MUTED


def _styles() -> dict[str, ParagraphStyle]:
    base = dict(fontName="Helvetica", textColor=INK, leading=13, fontSize=9.5)
    return {
        "h1": ParagraphStyle("h1", **{**base, "fontName": "Helvetica-Bold", "fontSize": 18,
                                      "leading": 22, "textColor": NAVY, "spaceAfter": 4}),
        "h2": ParagraphStyle("h2", **{**base, "fontName": "Helvetica-Bold", "fontSize": 12.5,
                                      "leading": 16, "textColor": NAVY, "spaceBefore": 10, "spaceAfter": 6}),
        "body": ParagraphStyle("body", **base),
        "muted": ParagraphStyle("muted", **{**base, "textColor": MUTED, "fontSize": 8.5, "leading": 11}),
        "cell": ParagraphStyle("cell", **{**base, "fontSize": 8.5, "leading": 10.5}),
        "cellb": ParagraphStyle("cellb", **{**base, "fontSize": 8.5, "leading": 10.5, "fontName": "Helvetica-Bold"}),
        "kpi_label": ParagraphStyle("kl", **{**base, "fontSize": 7.5, "leading": 9, "textColor": MUTED}),
        "kpi_value": ParagraphStyle("kv", **{**base, "fontName": "Helvetica-Bold", "fontSize": 14,
                                             "leading": 17, "textColor": NAVY}),
        "bullet": ParagraphStyle("bullet", **{**base, "leftIndent": 12, "bulletIndent": 0,
                                              "spaceAfter": 4, "alignment": TA_LEFT}),
        "find_title": ParagraphStyle("ft", **{**base, "fontName": "Helvetica-Bold", "fontSize": 10}),
    }


def _logo() -> ImageReader | None:
    for name in ("logo.png", "logo.jpeg", "logo.jpg"):
        path = BRAND_DIR / name
        if path.exists():
            try:
                return ImageReader(str(path))
            except Exception:  # noqa: BLE001 - a broken logo must not kill the report
                return None
    return None


# ------------------------------------------------------------------ pieces

def _kpi_grid(m: dict, prev: dict, cur: str, t: dict, st: dict) -> Table:
    items = [
        ("spend", t["spend"], _money(m.get("spend"), cur)),
        ("results", m.get("resultLabel") or t["results"], _num(m.get("results"))),
        ("costPerResult", t["cpr"], _money(m.get("costPerResult"), cur) if m.get("results") else t["none"]),
        ("ctr", t["ctr"], _pct(m.get("ctr"))),
        ("impressions", t["impressions"], _num(m.get("impressions"))),
        ("reach", t["reach"], _num(m.get("reach"))),
        ("cpm", t["cpm"], _money(m.get("cpm"), cur)),
        ("frequency", t["frequency"], _fmt(m.get('frequency'), 2)),
    ]
    cells = []
    for key, label, value in items:
        d = _delta(key, m, prev)
        block = [Paragraph(_safe(label).upper(), st["kpi_label"]), Paragraph(value, st["kpi_value"])]
        if d:
            block.append(Paragraph(f'<font color="{d[1].hexval().replace("0x", "#")}">{d[0]}</font>'
                                   f' <font color="#85847e" size="7">{t["vs_prev"]}</font>', st["muted"]))
        cells.append(block)
    rows = [cells[:4], cells[4:]]
    width = (PAGE_W - 2 * MARGIN) / 4
    table = Table(rows, colWidths=[width] * 4)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SOFT),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.white),
        ("LINEBELOW", (0, 0), (-1, 0), 3, colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return table


def _trend_charts(trend: list[dict], cur: str, t: dict, st: dict) -> list:
    if not trend:
        return []
    days = [r.get("date", "")[5:] for r in trend]
    spend = [float(r.get("spend") or 0) for r in trend]
    results = [float(r.get("results") or 0) for r in trend]
    width = PAGE_W - 2 * MARGIN
    step = max(1, len(days) // 10)
    labels = [d if i % step == 0 else "" for i, d in enumerate(days)]

    def bar() -> Drawing:
        d = Drawing(width, 150)
        c = VerticalBarChart()
        c.x, c.y, c.width, c.height = 40, 22, width - 50, 115
        c.data = [spend]
        c.bars[0].fillColor = BLUE
        c.bars[0].strokeColor = None
        c.barSpacing = 1
        c.groupSpacing = 3 if len(days) < 40 else 1
        c.categoryAxis.categoryNames = labels
        c.categoryAxis.labels.fontSize = 6.5
        c.categoryAxis.labels.fillColor = MUTED
        c.categoryAxis.strokeColor = LINE
        c.valueAxis.labels.fontSize = 6.5
        c.valueAxis.labels.fillColor = MUTED
        c.valueAxis.strokeColor = None
        c.valueAxis.gridStrokeColor = LINE
        c.valueAxis.visibleGrid = True
        c.valueAxis.valueMin = 0
        d.add(c)
        return d

    def line() -> Drawing:
        d = Drawing(width, 140)
        c = HorizontalLineChart()
        c.x, c.y, c.width, c.height = 40, 22, width - 50, 105
        c.data = [results]
        c.lines[0].strokeColor = GOLD
        c.lines[0].strokeWidth = 2.2
        c.categoryAxis.categoryNames = labels
        c.categoryAxis.labels.fontSize = 6.5
        c.categoryAxis.labels.fillColor = MUTED
        c.categoryAxis.strokeColor = LINE
        c.valueAxis.labels.fontSize = 6.5
        c.valueAxis.labels.fillColor = MUTED
        c.valueAxis.strokeColor = None
        c.valueAxis.gridStrokeColor = LINE
        c.valueAxis.visibleGrid = True
        c.valueAxis.valueMin = 0
        d.add(c)
        return d

    return [
        Paragraph(t["trend"], st["h2"]),
        Paragraph(f"{t['daily_spend']} ({cur})", st["muted"]), bar(),
        Paragraph(t["daily_results"], st["muted"]), line(),
    ]


def _table(header: list[str], rows: list[list], widths: list[float], st: dict, *, numeric_from: int = 2) -> Table:
    data = [[Paragraph(f'<font color="white"><b>{h}</b></font>', st["cell"]) for h in header]]
    for r in rows:
        data.append([c if isinstance(c, Paragraph) else Paragraph(_safe(c), st["cell"]) for c in r])
    table = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
        ("ALIGN", (numeric_from, 0), (-1, -1), "RIGHT"),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), SOFT))
    table.setStyle(TableStyle(style))
    return table


def _status(entity: dict, t: dict) -> str:
    s = entity.get("effective_status") or entity.get("status") or ""
    return t["statuses"].get(s, s.replace("_", " ").title())


def _category_bars(categories: dict, t: dict) -> Drawing:
    width = PAGE_W - 2 * MARGIN
    row_h = 18
    d = Drawing(width, row_h * len(categories) + 6)
    y = d.height - row_h
    for name, info in categories.items():
        score = int(info.get("score") or 0)
        color = GOOD if score >= 80 else GOLD if score >= 60 else BAD
        label = t["cat"].get(str(name), str(name))
        d.add(String(0, y + 5, _plain(label), fontName="Helvetica", fontSize=8.5, fillColor=INK))
        d.add(Rect(110, y + 3, width - 190, 9, fillColor=SOFT, strokeColor=None, rx=4, ry=4))
        d.add(Rect(110, y + 3, (width - 190) * score / 100, 9, fillColor=color, strokeColor=None, rx=4, ry=4))
        d.add(String(width - 68, y + 4, f"{score}/100", fontName="Helvetica-Bold", fontSize=8.5, fillColor=NAVY))
        findings = info.get("findings") or 0
        d.add(String(width - 4, y + 4, f"({findings})", fontName="Helvetica", fontSize=7.5,
                     fillColor=MUTED, textAnchor="end"))
        y -= row_h
    return d


def _finding(f: dict, cur: str, t: dict, st: dict) -> KeepTogether:
    sev = f.get("severity", "info")
    color = SEVERITY.get(sev, MUTED)
    hexc = color.hexval().replace("0x", "#")
    entity = f.get("entity") or {}
    head = (f'<font color="{hexc}"><b>{t["sev"].get(sev, sev).upper()}</b></font>'
            f'  <font color="#85847e" size="8">{_safe(t["cat"].get(str(f.get("category", "")), str(f.get("category", ""))))}'
            f' · {_safe(t["levels"].get(entity.get("level", ""), entity.get("level", "")))}: {_safe(entity.get("name", ""))}</font>')
    parts = [
        Paragraph(head, st["cell"]),
        Paragraph(_safe(f.get("title")), st["find_title"]),
        Paragraph(_safe(f.get("detail")), st["body"]),
    ]
    if f.get("recommendation"):
        parts.append(Paragraph(f'<b>{t["recommendation"]} :</b> {_safe(f["recommendation"])}'
                               if t is TEXT["fr"] else
                               f'<b>{t["recommendation"]}:</b> {_safe(f["recommendation"])}', st["body"]))
    if f.get("impact"):
        parts.append(Paragraph(f'<font color="#12805c"><b>{t["impact"]} : {_money(f["impact"], cur)}</b></font>',
                               st["cell"]))
    table = Table([["", parts]], colWidths=[4, PAGE_W - 2 * MARGIN - 4])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), color),
        ("BACKGROUND", (1, 0), (1, 0), SOFT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (1, 0), (1, 0), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return KeepTogether([table, Spacer(1, 6)])


def _takeaways(snapshot: dict, audit: dict, cur: str, t: dict) -> list[str]:
    m = snapshot.get("accountMetrics") or {}
    prev = snapshot.get("previousMetrics") or {}
    out = []
    delta = ""
    d = _delta("costPerResult", m, prev)
    if d and prev.get("costPerResult"):
        change = (float(m.get("costPerResult") or 0) - float(prev["costPerResult"])) / float(prev["costPerResult"]) * 100
        delta = t["delta_fmt"].format(sign="+" if change > 0 else "-", pct=f"{abs(change):.0f}")
    if m.get("results"):
        out.append(t["t_overview"].format(
            spend=_money(m.get("spend"), cur), results=_num(m.get("results")),
            label=(m.get("resultLabel") or t["results"]).lower(),
            cpr=_money(m.get("costPerResult"), cur), delta=delta))
    else:
        out.append(t["t_dead"].format(name=snapshot["account"].get("name") or "", spend=_money(m.get("spend"), cur)))

    camps = [c for c in snapshot.get("campaigns", []) if c.get("metrics")]
    winners = sorted((c for c in camps if (c["metrics"].get("results") or 0) >= 1),
                     key=lambda c: c["metrics"].get("costPerResult") or 1e12)
    if winners:
        w = winners[0]
        out.append(t["t_best"].format(name=w.get("name"), results=_num(w["metrics"]["results"]),
                                      cpr=_money(w["metrics"]["costPerResult"], cur)))
    dead = sorted((c for c in camps if (c["metrics"].get("spend") or 0) > 0 and not c["metrics"].get("results")),
                  key=lambda c: -(c["metrics"].get("spend") or 0))
    if dead:
        out.append(t["t_dead"].format(name=dead[0].get("name"), spend=_money(dead[0]["metrics"]["spend"], cur)))

    counts = audit.get("counts") or {}
    letter, label = _grade(audit, "fr" if t is TEXT["fr"] else "en")
    out.append(t["t_audit"].format(score=audit.get("score"), grade=f"{letter} · {label}".strip(" ·"),
                                   crit=counts.get("critical", 0), high=counts.get("high", 0)))
    if audit.get("estimatedWaste"):
        out.append(t["waste_line"].format(v=_money(audit["estimatedWaste"], cur)))
    first = next((f for f in audit.get("findings", []) if f.get("recommendation")), None)
    if first:
        out.append(t["t_top"].format(text=first["recommendation"]))
    return out


# ------------------------------------------------------------------ build

def build_report(snapshot: dict, audit: dict, *, window_label: str, lang: str = "fr") -> bytes:
    t = TEXT.get(lang, TEXT["fr"])
    lang = "fr" if t is TEXT["fr"] else "en"
    token = _LANG.set(lang)
    st = _styles()
    cur = snapshot.get("currency", "USD")
    account = snapshot.get("account") or {}
    name = account.get("name") or account.get("id") or ""
    m = snapshot.get("accountMetrics") or {}
    prev = snapshot.get("previousMetrics") or {}
    logo = _logo()
    generated = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    width = PAGE_W - 2 * MARGIN

    def cover(c, doc):
        c.saveState()
        c.setFillColor(NAVY)
        c.rect(0, 0, PAGE_W, PAGE_H * 0.42, stroke=0, fill=1)
        c.setFillColor(GOLD)
        c.rect(0, PAGE_H * 0.42, PAGE_W, 4, stroke=0, fill=1)
        if logo:
            iw, ih = logo.getSize()
            w = 62 * mm
            c.drawImage(logo, MARGIN, PAGE_H - MARGIN - w * ih / iw, w, w * ih / iw, mask="auto")
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 28)
        c.drawString(MARGIN, PAGE_H * 0.66, t["title"])
        c.setFont("Helvetica", 15)
        c.setFillColor(INK)
        c.drawString(MARGIN, PAGE_H * 0.66 - 26, _plain(name))
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 10.5)
        c.drawString(MARGIN, PAGE_H * 0.66 - 50, f"{t['period']} : {_plain(window_label)}"
                     if lang == "fr" else f"{t['period']}: {_plain(window_label)}")
        c.drawString(MARGIN, PAGE_H * 0.66 - 66, f"{t['generated']} {generated}")

        # Health score ring
        score = int(audit.get("score") or 0)
        cx, cy, r = PAGE_W - MARGIN - 52, PAGE_H * 0.21, 44
        c.setLineWidth(9)
        c.setStrokeColor(colors.HexColor("#24456f"))
        c.circle(cx, cy, r, stroke=1, fill=0)
        ring = GOOD if score >= 80 else GOLD if score >= 60 else colors.HexColor("#ff6b5a")
        c.setStrokeColor(ring)
        # A zero-length arc divides by zero inside reportlab: score 0 draws no ring.
        if score > 0:
            c.arc(cx - r, cy - r, cx + r, cy + r, 90, -3.6 * min(score, 99.9))
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 26)
        c.drawCentredString(cx, cy - 4, str(score))
        c.setFont("Helvetica", 8.5)
        letter, label = _grade(audit, lang)
        c.drawCentredString(cx, cy - 18, f"/100 · {letter}")
        if label:
            c.setFont("Helvetica", 7.5)
            c.drawCentredString(cx, cy - r - 16, _plain(label).upper())
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(cx, cy + r + 14, t["health"].upper())

        # Three headline numbers on the navy band
        c.setFillColor(colors.white)
        headline = [
            (t["spend"], _money(m.get("spend"), cur)),
            (m.get("resultLabel") or t["results"], _num(m.get("results"))),
            (t["cpr"], _money(m.get("costPerResult"), cur) if m.get("results") else t["none"]),
        ]
        x = MARGIN
        for label, value in headline:
            c.setFont("Helvetica", 8.5)
            c.setFillColor(colors.HexColor("#a9bad3"))
            c.drawString(x, PAGE_H * 0.27, _plain(label).upper())
            c.setFont("Helvetica-Bold", 17)
            c.setFillColor(colors.white)
            c.drawString(x, PAGE_H * 0.27 - 22, value)
            x += 50 * mm
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#a9bad3"))
        c.drawString(MARGIN, MARGIN, f"{t['confidential']} {_plain(name)}")
        c.restoreState()

    def page(c, doc):
        c.saveState()
        if logo:
            iw, ih = logo.getSize()
            w = 22 * mm
            c.drawImage(logo, MARGIN, PAGE_H - 8 * mm - w * ih / iw, w, w * ih / iw, mask="auto")
        c.setFont("Helvetica", 8)
        c.setFillColor(MUTED)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - 15 * mm, f"{_plain(name)} · {_plain(window_label)}")
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(MARGIN, PAGE_H - 19 * mm, PAGE_W - MARGIN, PAGE_H - 19 * mm)
        c.setFillColor(GOLD)
        c.rect(MARGIN, 10 * mm, 18, 2, stroke=0, fill=1)
        c.setFillColor(MUTED)
        c.drawRightString(PAGE_W - MARGIN, 10 * mm, f"{t['page']} {doc.page}")
        c.restoreState()

    story: list = [Spacer(1, 1), PageBreak()]

    # ---- summary
    story += [Paragraph(t["summary"], st["h1"])]
    s = {
        "c": len(snapshot.get("campaigns", [])), "a": len(snapshot.get("adsets", [])),
        "d": len(snapshot.get("ads", [])),
        "ac": sum(1 for c in snapshot.get("campaigns", []) if c.get("status") == "ACTIVE"),
    }
    story += [Paragraph(_safe(t["structure"].format(**s)), st["muted"]), Spacer(1, 8)]
    story += [Paragraph(t["kpis"], st["h2"]), _kpi_grid(m, prev, cur, t, st)]
    story += [Paragraph(t["takeaways"], st["h2"])]
    for line in _takeaways(snapshot, audit, cur, t):
        story.append(Paragraph(_safe(line), st["bullet"], bulletText="•"))
    story += _trend_charts(snapshot.get("trend") or [], cur, t, st)

    # ---- campaigns
    camps = sorted((c for c in snapshot.get("campaigns", []) if c.get("metrics")),
                   key=lambda c: -(c["metrics"].get("spend") or 0))
    total = float(m.get("spend") or 0) or 1
    if camps:
        story += [PageBreak(), Paragraph(t["campaigns"], st["h1"])]
        rows = []
        for c in camps[:25]:
            cm = c["metrics"]
            rows.append([
                Paragraph(f"<b>{_safe(c.get('name'))}</b>", st["cell"]), _status(c, t),
                _money(cm.get("spend"), cur), _num(cm.get("results")),
                _money(cm.get("costPerResult"), cur) if cm.get("results") else t["none"],
                _pct(cm.get("ctr")), (_fmt(float(cm.get('spend') or 0) / total * 100, 0) + (" %" if lang == "fr" else "%")),
            ])
        story.append(_table(
            [t["name"], t["status"], t["spend"], t["results"], t["cpr"], t["ctr"], t["share"]],
            rows, [width * x for x in (0.30, 0.11, 0.14, 0.11, 0.15, 0.09, 0.10)], st))

    # ---- ads
    ads = [a for a in snapshot.get("ads", []) if a.get("metrics")]
    best = sorted((a for a in ads if (a["metrics"].get("results") or 0) >= 1),
                  key=lambda a: a["metrics"].get("costPerResult") or 1e12)[:10]
    waste = sorted((a for a in ads if (a["metrics"].get("spend") or 0) > 0 and not a["metrics"].get("results")),
                   key=lambda a: -(a["metrics"].get("spend") or 0))[:10]

    def ad_rows(items):
        return [[
            Paragraph(f"<b>{_safe(a.get('name'))}</b>", st["cell"]), _status(a, t),
            _money(a["metrics"].get("spend"), cur), _num(a["metrics"].get("results")),
            _money(a["metrics"].get("costPerResult"), cur) if a["metrics"].get("results") else t["none"],
            _pct(a["metrics"].get("ctr")),
        ] for a in items]

    ad_widths = [width * x for x in (0.38, 0.12, 0.15, 0.10, 0.15, 0.10)]
    ad_header = [t["name"], t["status"], t["spend"], t["results"], t["cpr"], t["ctr"]]
    if best:
        story += [Paragraph(t["ads_best"], st["h2"]), _table(ad_header, ad_rows(best), ad_widths, st)]
    if waste:
        story += [Paragraph(t["ads_waste"], st["h2"]), _table(ad_header, ad_rows(waste), ad_widths, st)]

    # ---- audit
    story += [PageBreak(), Paragraph(t["audit"], st["h1"])]
    if audit.get("categories"):
        story += [Paragraph(t["categories"], st["h2"]), _category_bars(audit["categories"], t)]
    story += [Paragraph(t["findings"], st["h2"])]
    findings = audit.get("findings") or []
    if not findings:
        story.append(Paragraph(t["no_findings"], st["body"]))
    for f in findings:
        story.append(_finding(f, cur, t, st))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=25 * mm, bottomMargin=18 * mm,
        title=f"{t['title']} — {_plain(name)}", author="ULTEx", subject=_plain(window_label),
    )
    try:
        doc.build(story, onFirstPage=cover, onLaterPages=page)
    finally:
        _LANG.reset(token)
    return buffer.getvalue()


def _plain(text: Any) -> str:
    """For canvas strings (no markup): same character filtering as _safe."""
    return ("" if text is None else str(text)).encode("cp1252", "replace").decode("cp1252")
