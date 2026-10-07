import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ChevronDown,
  ChevronRight,
  CircleCheck,
  Search,
  SlidersHorizontal,
} from "lucide-react";

import { useAudit } from "../App.jsx";
import Skeleton from "../components/Skeleton.jsx";
import { money, percent, SEVERITY_COLOR, SEVERITY_ORDER } from "../lib/format.js";
import { currentLang, useT } from "../lib/i18n.jsx";

/**
 * The audit page.
 *
 * Built around triage rather than enumeration. A flat list makes "this account
 * cannot pay its bills" a peer of "26 ads never got enough impressions", which
 * is how audit tools get skimmed and closed. So the page answers three
 * questions in order: what do I fix first, where is the damage concentrated,
 * and then the full list.
 *
 * Repeats still collapse by rule -- the starved-ads rule alone fires 26 times
 * on a real account -- and each group carries the money it is estimated to be
 * costing, which is what makes the ordering defensible rather than a hunch.
 */

const SORTS = [
  { key: "impact", label: "Recoverable spend" },
  { key: "severity", label: "Severity" },
  { key: "count", label: "Entities affected" },
];

/** Findings grouped by the rule that produced them. */
function groupByRule(findings) {
  const byRule = new Map();
  for (const f of findings) {
    if (!byRule.has(f.ruleId)) {
      byRule.set(f.ruleId, {
        ruleId: f.ruleId,
        title: f.title,
        category: f.category,
        severity: f.severity,
        findings: [],
        impact: 0,
      });
    }
    const group = byRule.get(f.ruleId);
    group.findings.push(f);
    group.impact += f.impact || 0;
  }
  return [...byRule.values()];
}

export default function Findings() {
  const { data } = useAudit();
  const t = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [sort, setSort] = useState("impact");
  const [open, setOpen] = useState({});
  const [expandAll, setExpandAll] = useState(false);

  // Hooks first -- nothing may return above them.
  const audit = data?.audit;
  const currency = data?.account?.currency || "USD";
  const severity = searchParams.get("severity") || "all";

  const allGroups = useMemo(() => (audit ? groupByRule(audit.findings) : []), [audit]);

  /**
   * The standing answer to "what first", deliberately ignoring the filters
   * below -- filtering the list should not change the advice.
   */
  const topActions = useMemo(
    () =>
      [...allGroups]
        .filter((g) => g.impact > 0 || g.severity === "critical")
        .sort((a, b) => {
          const aCrit = a.severity === "critical";
          const bCrit = b.severity === "critical";
          // A critical with no price tag still outranks an expensive medium:
          // "the account cannot spend" is not a budgeting question.
          if (aCrit !== bCrit) return aCrit ? -1 : 1;
          return b.impact - a.impact;
        })
        .slice(0, 3),
    [allGroups]
  );

  const categoryStats = useMemo(() => {
    const stats = new Map();
    for (const g of allGroups) {
      const entry =
        stats.get(g.category) || { category: g.category, count: 0, impact: 0, worst: "info" };
      entry.count += g.findings.length;
      entry.impact += g.impact;
      if (SEVERITY_ORDER.indexOf(g.severity) < SEVERITY_ORDER.indexOf(entry.worst)) {
        entry.worst = g.severity;
      }
      stats.set(g.category, entry);
    }
    return [...stats.values()].sort((a, b) => b.impact - a.impact || b.count - a.count);
  }, [allGroups]);

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = allGroups
      .map((g) => ({
        ...g,
        findings: g.findings.filter((f) => {
          if (!needle) return true;
          return (
            f.title.toLowerCase().includes(needle) ||
            f.entity.name?.toLowerCase().includes(needle) ||
            f.detail.toLowerCase().includes(needle)
          );
        }),
      }))
      .filter((g) => g.findings.length > 0)
      .filter((g) => severity === "all" || g.severity === severity)
      .filter((g) => category === "all" || g.category === category);

    return filtered.sort((a, b) => {
      if (sort === "count") return b.findings.length - a.findings.length;
      if (sort === "severity") {
        const s = SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity);
        return s !== 0 ? s : b.impact - a.impact;
      }
      const aCrit = a.severity === "critical";
      const bCrit = b.severity === "critical";
      if (aCrit !== bCrit) return aCrit ? -1 : 1;
      return b.impact - a.impact;
    });
  }, [allGroups, query, severity, category, sort]);

  if (!data) return <Skeleton />;

  const shown = groups.reduce((s, g) => s + g.findings.length, 0);
  const maxImpact = Math.max(1, ...allGroups.map((g) => g.impact));
  const total = audit.findings.length;
  const filtering = severity !== "all" || category !== "all" || query.trim() !== "";

  function setSeverity(value) {
    const next = new URLSearchParams(searchParams);
    if (value === "all") next.delete("severity");
    else next.set("severity", value);
    setSearchParams(next, { replace: true });
  }

  function clearFilters() {
    setQuery("");
    setCategory("all");
    setSeverity("all");
  }

  function toggleAll() {
    const next = !expandAll;
    setExpandAll(next);
    setOpen(Object.fromEntries(groups.map((g) => [g.ruleId, next])));
  }

  return (
    <div className="audit">
      {/* 1 — the distribution, and what it is costing. */}
      <section className="card card-pad triage">
        <div className="triage-main">
          <div className="triage-head">
            <span className="triage-count">{total}</span>
            <span className="triage-label">
              {total === 1 ? t("finding") : t("findings")}
              <span className="triage-sub">{t("from {n} rules", { n: audit.rulesRun })}</span>
            </span>
          </div>

          {/* Stacked part-to-whole using the reserved status colours, which is
              exactly what they are for. Each segment filters the list. */}
          <div className="sev-bar" role="img" aria-label={t("Findings by severity")}>
            {SEVERITY_ORDER.filter((s) => audit.counts[s] > 0).map((s) => (
              <button
                key={s}
                className={`sev-seg ${severity === s ? "is-on" : ""}`}
                style={{ flexGrow: audit.counts[s], background: SEVERITY_COLOR[s] }}
                onClick={() => setSeverity(severity === s ? "all" : s)}
                title={`${audit.counts[s]} ${t(s)} — ${t("click to filter")}`}
                aria-label={`${audit.counts[s]} ${t(s)}`}
              />
            ))}
          </div>

          <div className="sev-legend">
            {SEVERITY_ORDER.filter((s) => audit.counts[s] > 0).map((s) => (
              <button
                key={s}
                className={`sev-key ${severity === s ? "is-on" : ""}`}
                onClick={() => setSeverity(severity === s ? "all" : s)}
              >
                <span className="dot" style={{ background: SEVERITY_COLOR[s] }} />
                <span className="sev-key-n">{audit.counts[s]}</span>
                <span className="sev-key-l">{t(s)}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="triage-money">
          <span className="triage-money-label">{t("Recoverable spend")}</span>
          <span className="triage-amount">{money(audit.estimatedWaste, currency)}</span>
          <span className="triage-sub">
            {t("{pct} of spend this window", {
              pct: percent((audit.estimatedWaste / (data.summary.metrics.spend || 1)) * 100, 0),
            })}
          </span>
        </div>
      </section>

      {/* 2 — what to do first. */}
      {topActions.length > 0 && (
        <section>
          <h2 className="section-title">{t("Fix these first")}</h2>
          <div className="priority-grid">
            {topActions.map((g, i) => (
              <article
                key={g.ruleId}
                className="card priority-card"
                style={{ "--sev": SEVERITY_COLOR[g.severity] }}
              >
                <div className="priority-top">
                  <span className="priority-rank">{i + 1}</span>
                  <span className="sev-pill" style={{ background: SEVERITY_COLOR[g.severity] }}>
                    {t(g.severity)}
                  </span>
                  {g.impact > 0 && (
                    <span className="priority-impact">{money(g.impact, currency)}</span>
                  )}
                </div>
                <div className="priority-title">{g.title}</div>
                <div className="priority-meta">
                  {g.findings.length === 1
                    ? g.findings[0].entity.name
                    : `${t("{n} entities", { n: g.findings.length })} · ${t(g.category)}`}
                </div>
                <p className="priority-fix">{g.findings[0].recommendation}</p>
              </article>
            ))}
          </div>
        </section>
      )}

      {/* 3 — where the damage sits. Doubles as the category filter. */}
      {categoryStats.length > 1 && (
        <section>
          <h2 className="section-title">{t("By category")}</h2>
          <div className="cat-row">
            {categoryStats.map((c) => (
              <button
                key={c.category}
                className={`cat-card ${category === c.category ? "is-on" : ""}`}
                onClick={() => setCategory(category === c.category ? "all" : c.category)}
              >
                <span className="cat-name">{t(c.category)}</span>
                <span className="cat-nums">
                  <span className="cat-count">
                    {c.count} {c.count === 1 ? t("finding") : t("findings")}
                  </span>
                  {c.impact > 0 && (
                    <span className="cat-impact">{money(c.impact, currency)}</span>
                  )}
                </span>
                <span className="cat-bar">
                  <span
                    className="cat-bar-fill"
                    style={{
                      width: `${Math.max(3, (c.impact / maxImpact) * 100)}%`,
                      background: SEVERITY_COLOR[c.worst],
                    }}
                  />
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      {/* 4 — the full list. */}
      <section>
        <div className="list-toolbar">
          <div className="field-search">
            <Search size={14} />
            <input
              className="field-search-input"
              placeholder={t("Search findings, campaigns, ad sets…")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          <label className="toolbar-sort">
            <SlidersHorizontal size={14} />
            <select
              className="select"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              aria-label={t("Sort findings")}
            >
              {SORTS.map((s) => (
                <option key={s.key} value={s.key}>
                  {t(s.label)}
                </option>
              ))}
            </select>
          </label>

          <button className="btn" onClick={toggleAll}>
            {expandAll ? t("Collapse all") : t("Expand all")}
          </button>
        </div>

        <div className="list-count">
          <span>
            {t("Showing")} <strong>{shown}</strong> {t("of")} {total} {total === 1 ? t("finding") : t("findings")}
          </span>
          {filtering && (
            <button className="link-btn" onClick={clearFilters}>
              {t("Clear filters")}
            </button>
          )}
        </div>

        <div className="finding-list">
          {groups.map((group) => {
            const isOpen =
              open[group.ruleId] ?? ["critical", "high"].includes(group.severity);
            const first = group.findings[0];
            return (
              <article
                key={group.ruleId}
                className={`finding ${isOpen ? "is-open" : ""}`}
                style={{ "--sev": SEVERITY_COLOR[group.severity] }}
              >
                <button
                  className="finding-head"
                  onClick={() => setOpen((o) => ({ ...o, [group.ruleId]: !isOpen }))}
                  aria-expanded={isOpen}
                >
                  <span className="finding-caret">
                    {isOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                  </span>

                  <span className="finding-main">
                    <span className="finding-title-row">
                      <span className="finding-title">{group.title}</span>
                      <span
                        className="sev-pill"
                        style={{ background: SEVERITY_COLOR[group.severity] }}
                      >
                        {t(group.severity)}
                      </span>
                      {group.findings.length > 1 && (
                        <span className="tag">{t("{n} affected", { n: group.findings.length })}</span>
                      )}
                      <span className="finding-cat">{t(group.category)}</span>
                    </span>
                    <span className="finding-summary">
                      {group.findings.length === 1
                        ? first.detail
                        : t("{name} and {n} others", { name: first.entity.name, n: group.findings.length - 1 })}
                    </span>
                  </span>

                  {group.impact > 0 && (
                    <span className="finding-impact">
                      <span className="finding-impact-value">
                        {money(group.impact, currency)}
                      </span>
                      <span className="impact-bar">
                        <span
                          className="impact-bar-fill"
                          style={{ width: `${(group.impact / maxImpact) * 100}%` }}
                        />
                      </span>
                    </span>
                  )}
                </button>

                {isOpen && (
                  <div className="finding-body">
                    {group.findings.map((f, i) => (
                      <div className="entity" key={`${f.entity.id}-${i}`}>
                        <div className="entity-head">
                          <span className="tag">{t(f.entity.level)}</span>
                          <span className="entity-name">{f.entity.name}</span>
                          {f.impact > 0 && (
                            <span className="entity-impact">{money(f.impact, currency)}</span>
                          )}
                        </div>
                        <p className="entity-detail">{f.detail}</p>
                        <Evidence evidence={f.evidence} />
                        <p className="entity-fix">
                          <CircleCheck size={13} />
                          <span>{f.recommendation}</span>
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </article>
            );
          })}

          {groups.length === 0 && (
            <div className="card empty">
              {total === 0
                ? t("No findings — this account passed every rule.")
                : t("No findings match these filters.")}
              {filtering && total > 0 && (
                <div style={{ marginTop: 12 }}>
                  <button className="btn" onClick={clearFilters}>
                    {t("Clear filters")}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

/**
 * The raw values a rule fired on.
 *
 * Every rule already records these and nothing displayed them before. Showing
 * them is what lets someone check a finding instead of taking it on trust --
 * "where did that number come from" is the first thing asked when a figure is
 * disputed.
 */
function Evidence({ evidence }) {
  const entries = Object.entries(evidence || {}).filter(
    ([, v]) => v !== null && v !== undefined && typeof v !== "object"
  );
  if (!entries.length) return null;
  // Pinned to en-US like every other number in the app. Bare toLocaleString()
  // follows the browser locale, so the same page rendered "52,5" here next to
  // "52.5" everywhere else.
  const show = (v) =>
    typeof v === "number"
      ? v.toLocaleString(currentLang() === "fr" ? "fr-FR" : "en-US", { maximumFractionDigits: 2 })
      : String(v);

  return (
    <dl className="evidence">
      {entries.map(([k, v]) => (
        <div className="evidence-item" key={k}>
          <dt>{k.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2")}</dt>
          <dd>{show(v)}</dd>
        </div>
      ))}
    </dl>
  );
}
