import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Info } from "lucide-react";

import { useAudit } from "../App.jsx";
import Skeleton from "../components/Skeleton.jsx";
import BarList from "../charts/BarList.jsx";
import { count, money, percent, SEVERITY_COLOR, SEVERITY_ORDER } from "../lib/format.js";
import { useT } from "../lib/i18n.jsx";

/**
 * Several accounts side by side.
 *
 * The money rule from the backend surfaces here: when the selection spans more
 * than one currency there is no single "total spend" to show, so spend is
 * listed per currency and the comparison bars fall back to a unit-free metric.
 * Showing one summed figure would silently add dirhams to dollars.
 */
export default function Portfolio({ tab = "overview" }) {
  const { data, search } = useAudit();
  const t = useT();
  const [sortKey, setSortKey] = useState("spend");

  const accounts = data?.accounts ?? [];

  const rows = useMemo(
    () =>
      [...accounts].sort((a, b) => {
        if (sortKey === "score") return a.score - b.score;
        if (sortKey === "results") return b.metrics.results - a.metrics.results;
        return b.metrics.spend - a.metrics.spend;
      }),
    [accounts, sortKey]
  );

  if (!data) return <Skeleton />;

  const { combined, counts, estimatedWasteByCurrency, failed = [], findings } = data;
  const mixed = combined.mixedCurrency;

  if (tab === "findings") {
    return <PortfolioFindings findings={findings} counts={counts} search={search} />;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {failed.length > 0 && (
        <div className="warn-box">
          <Info size={16} />
          <span>
            <strong>{t("{n} account(s) could not be loaded.", { n: failed.length })}</strong>{" "}
            {failed.map((f) => `${f.accountId}: ${f.error}`).join(" · ")}
          </span>
        </div>
      )}

      {mixed && (
        <div className="warn-box">
          <Info size={16} />
          <span>
            <strong>{t("Mixed currencies ({list}).", { list: combined.currencies.join(", ") })}</strong>{" "}
            {t("Spend and cost figures are shown per currency and never added together — a combined total would be meaningless. Counts and rates (results, clicks, CTR) do combine.")}
          </span>
        </div>
      )}

      {/* Headline row */}
      <section className="grid" style={{ gridTemplateColumns: "minmax(240px,300px) 1fr" }}>
        <div className="card card-pad">
          <div className="stat-label">{t("Average health")}</div>
          <div
            style={{
              fontSize: 48,
              fontWeight: 700,
              letterSpacing: "-0.03em",
              lineHeight: 1.05,
              marginTop: 4,
              color:
                data.averageScore >= 75
                  ? "var(--good)"
                  : data.averageScore >= 50
                    ? "var(--warning)"
                    : "var(--critical)",
            }}
          >
            {data.averageScore}
            <span style={{ fontSize: 15, color: "var(--text-muted)", fontWeight: 500 }}> / 100</span>
          </div>
          <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
            {t("across {n} accounts · unweighted", { n: accounts.length })}
          </div>

          <div style={{ marginTop: 14, display: "flex", flexWrap: "wrap", gap: 8 }}>
            {SEVERITY_ORDER.filter((s) => counts[s] > 0).map((s) => (
              <Link
                key={s}
                to={{ pathname: "findings", search }}
                className="tag"
                style={{ textTransform: "capitalize" }}
              >
                <span className="dot" style={{ background: SEVERITY_COLOR[s] }} />
                {counts[s]} {t(s)}
              </Link>
            ))}
          </div>

          <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--border)" }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{t("Recoverable spend")}</div>
            {Object.entries(estimatedWasteByCurrency).map(([cur, v]) => (
              <div key={cur} style={{ fontSize: 18, fontWeight: 600, color: "var(--serious)" }}>
                {money(v, cur)}
              </div>
            ))}
          </div>
        </div>

        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(158px,1fr))", gap: 12 }}>
          <Tile label={t("Accounts")} value={String(accounts.length)} sub={combined.currencies.join(" · ")} />
          <Tile
            label={t("Spend")}
            value={
              mixed ? (
                <span style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                  {Object.entries(combined.spendByCurrency).map(([c, v]) => (
                    <span key={c} style={{ fontSize: 17 }}>{money(v, c)}</span>
                  ))}
                </span>
              ) : (
                money(combined.spend, combined.currency)
              )
            }
            sub={mixed ? t("per currency") : undefined}
          />
          <Tile label={t("Results")} value={count(combined.results)} />
          <Tile label={t("Impressions")} value={count(combined.impressions, { compact: true })} />
          <Tile label={t("Clicks")} value={count(combined.clicks)} sub={`CTR ${percent(combined.ctr)}`} />
          <Tile
            label={t("Cost per result")}
            value={
              <span style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                {Object.entries(combined.costPerResultByCurrency).map(([c, v]) => (
                  <span key={c} style={{ fontSize: 17 }}>{money(v, c)}</span>
                ))}
              </span>
            }
            sub={mixed ? t("per currency") : undefined}
          />
        </div>
      </section>

      {/* Comparison chart. Under mixed currencies the money axis is dropped for
          a unit-free one -- bars of dollars beside dirhams compare nothing. */}
      <section className="card card-pad">
        <div className="card-head">
          <span className="card-title">
            {mixed ? t("Results by account") : t("Spend by account")}
          </span>
          <span className="card-sub">
            {mixed ? t("counts compare across currencies; spend does not") : t(data.window?.label || "")}
          </span>
        </div>
        <BarList
          rows={rows.map((a) => ({
            id: a.accountId,
            key: a.accountName,
            spend: a.metrics.spend,
            results: a.metrics.results,
          }))}
          valueKey={mixed ? "results" : "spend"}
          formatValue={(v) =>
            mixed ? count(v) : money(v, combined.currency || rows[0]?.currency)
          }
          emptyMessage={t("No delivery in this window")}
        />
      </section>

      {/* Per-account table */}
      <section className="card" style={{ overflowX: "auto" }}>
        <table style={{ minWidth: 860 }}>
          <thead>
            <tr>
              <th className="no-sort">{t("Account")}</th>
              <th onClick={() => setSortKey("score")} className="num">{t("Health")}</th>
              <th onClick={() => setSortKey("spend")} className="num">{t("Spend")}</th>
              <th onClick={() => setSortKey("results")} className="num">{t("Results")}</th>
              <th className="no-sort num">{t("Cost/result")}</th>
              <th className="no-sort num">CTR</th>
              <th className="no-sort num">{t("Findings")}</th>
              <th className="no-sort num">{t("Recoverable")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => {
              const total = SEVERITY_ORDER.reduce((s, k) => s + (a.counts[k] || 0), 0);
              return (
                <tr key={a.accountId}>
                  <td>
                    <Link
                      to={{ pathname: `/a/${a.accountId}`, search }}
                      style={{ fontWeight: 600, color: "var(--accent)" }}
                    >
                      {a.accountName}
                    </Link>
                    <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                      {a.accountId} · {a.currency}
                    </div>
                  </td>
                  <td className="num">
                    <span
                      style={{
                        fontWeight: 700,
                        color:
                          a.score >= 75
                            ? "var(--good)"
                            : a.score >= 50
                              ? "var(--warning)"
                              : "var(--critical)",
                      }}
                    >
                      {a.score}
                    </span>
                    <span style={{ color: "var(--text-muted)" }}> {a.grade.letter}</span>
                  </td>
                  <td className="num">{money(a.metrics.spend, a.currency)}</td>
                  <td className="num">{count(a.metrics.results)}</td>
                  <td className="num">
                    {a.metrics.results > 0 ? money(a.metrics.costPerResult, a.currency) : "—"}
                  </td>
                  <td className="num">{percent(a.metrics.ctr)}</td>
                  <td className="num">{total}</td>
                  <td className="num" style={{ color: "var(--serious)" }}>
                    {a.estimatedWaste > 0 ? money(a.estimatedWaste, a.currency) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function Tile({ label, value, sub }) {
  return (
    <div className="card card-pad stat-tile">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

/** Merged findings across accounts, each tagged with where it came from. */
function PortfolioFindings({ findings, counts, search }) {
  const t = useT();
  const [severity, setSeverity] = useState("all");
  const visible = findings.filter((f) => severity === "all" || f.severity === severity);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="card card-pad" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          className={`chip ${severity === "all" ? "active" : ""}`}
          onClick={() => setSeverity("all")}
        >
          {t("All")} {findings.length}
        </button>
        {SEVERITY_ORDER.filter((s) => counts[s] > 0).map((s) => (
          <button
            key={s}
            className={`chip ${severity === s ? "active" : ""}`}
            onClick={() => setSeverity(s)}
            style={{ textTransform: "capitalize" }}
          >
            <span className="dot" style={{ background: SEVERITY_COLOR[s] }} />
            {t(s)} {counts[s]}
          </button>
        ))}
      </div>

      {visible.map((f, i) => (
        <div key={`${f.account.id}-${f.ruleId}-${i}`} className="card card-pad">
          <div style={{ display: "flex", gap: 9, alignItems: "center", flexWrap: "wrap" }}>
            <span className="dot" style={{ background: SEVERITY_COLOR[f.severity] }} />
            <strong>{f.title}</strong>
            <Link
              to={{ pathname: `/a/${f.account.id}`, search }}
              className="tag"
              style={{ textTransform: "none" }}
            >
              {f.account.name}
            </Link>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{t(f.category)}</span>
          </div>
          <p style={{ marginTop: 6, fontSize: 13, color: "var(--text-secondary)" }}>{f.detail}</p>
          <p style={{ marginTop: 4, fontSize: 13, color: "var(--accent)" }}>
            → {f.recommendation}
          </p>
        </div>
      ))}

      {!visible.length && <div className="card empty">{t("No findings at this severity.")}</div>}
    </div>
  );
}
