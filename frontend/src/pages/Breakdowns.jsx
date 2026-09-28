import { useEffect, useMemo, useState } from "react";
import { useAudit } from "../App.jsx";
import { api } from "../lib/api.js";
import BarList from "../charts/BarList.jsx";
import GroupedBars from "../charts/GroupedBars.jsx";
import { count, money, percent, titleCase } from "../lib/format.js";

const CUTS = [
  { key: "placement", label: "Placement" },
  { key: "platform", label: "Platform" },
  { key: "device", label: "Device" },
  { key: "age_gender", label: "Age & gender" },
  { key: "country", label: "Country" },
  { key: "hour", label: "Hour of day" },
];

const METRICS = [
  { key: "spend", label: "Spend", format: (v, c) => money(v, c) },
  { key: "results", label: "Results", format: (v) => count(v) },
  { key: "costPerResult", label: "Cost per result", format: (v, c) => money(v, c) },
  { key: "ctr", label: "CTR", format: (v) => percent(v) },
  { key: "cpm", label: "CPM", format: (v, c) => money(v, c) },
];

export default function Breakdowns() {
  const { accountId, window: win, data } = useAudit();
  const [cut, setCut] = useState("placement");
  const [metric, setMetric] = useState("spend");
  const [state, setState] = useState({ loading: true });

  const cur = data?.account?.currency || "USD";

  useEffect(() => {
    let cancelled = false;
    setState({ loading: true });
    api
      .breakdown(accountId, cut, win)
      .then((result) => !cancelled && setState({ loading: false, ...result }))
      .catch((error) => !cancelled && setState({ loading: false, error }));
    return () => {
      cancelled = true;
    };
  }, [accountId, cut, win]);

  const rows = state.rows || [];
  const activeMetric = METRICS.find((m) => m.key === metric) || METRICS[0];

  // Cost-per-result rows only mean something where results actually landed;
  // a 0.00 CPR on a row with no conversions would sort to the top and read as
  // "cheapest", which is the opposite of the truth.
  const chartRows = useMemo(() => {
    const base = metric === "costPerResult" ? rows.filter((r) => r.results > 0) : rows;
    return [...base]
      .sort((a, b) => (b[metric] || 0) - (a[metric] || 0))
      .slice(0, 12)
      .map((r) => ({ ...r, id: r.key }));
  }, [rows, metric]);

  // Age x gender is the one cut with a genuine second dimension, so it gets
  // grouped columns instead of a flat ranking.
  const ageGender = useMemo(() => {
    if (cut !== "age_gender") return null;
    const byAge = new Map();
    for (const row of rows) {
      const age = row.dimensions?.age || "unknown";
      const gender = row.dimensions?.gender || "unknown";
      if (!byAge.has(age)) byAge.set(age, { label: age, values: {} });
      byAge.get(age).values[gender] = (byAge.get(age).values[gender] || 0) + (row[metric] || 0);
    }
    return [...byAge.values()].sort((a, b) => a.label.localeCompare(b.label));
  }, [cut, rows, metric]);

  const best = chartRows.length ? chartRows[chartRows.length - 1] : null;
  const worst = chartRows.length ? chartRows[0] : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="card card-pad" style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {CUTS.map((c) => (
            <button
              key={c.key}
              className={`chip ${cut === c.key ? "active" : ""}`}
              onClick={() => setCut(c.key)}
            >
              {c.label}
            </button>
          ))}
        </div>
        <select
          className="select"
          value={metric}
          onChange={(e) => setMetric(e.target.value)}
          style={{ marginLeft: "auto" }}
        >
          {METRICS.map((m) => (
            <option key={m.key} value={m.key}>
              {m.label}
            </option>
          ))}
        </select>
      </div>

      {state.error && (
        <div className="error-box">
          <strong>Could not load this breakdown.</strong> {state.error.message}
        </div>
      )}

      {state.loading ? (
        <div className="card empty">Loading breakdown…</div>
      ) : (
        <>
          {cut === "age_gender" && ageGender ? (
            <div className="card card-pad">
              <div className="card-head">
                <span className="card-title">
                  {activeMetric.label} by age and gender
                </span>
                <span className="card-sub">{data?.window?.label ?? ""}</span>
              </div>
              <GroupedBars
                groups={ageGender}
                series={[
                  { key: "male", name: "Male", color: "var(--series-1)" },
                  { key: "female", name: "Female", color: "var(--series-2)" },
                ]}
                formatValue={(v, o) =>
                  o?.axis
                    ? activeMetric.format(v, cur).replace(/\.\d+/, "")
                    : activeMetric.format(v, cur)
                }
                emptyMessage="No age/gender data in this window"
              />
            </div>
          ) : (
            <div className="card card-pad">
              <div className="card-head">
                <span className="card-title">
                  {activeMetric.label} by {CUTS.find((c) => c.key === cut)?.label.toLowerCase()}
                </span>
                <span className="card-sub">top {chartRows.length}</span>
              </div>
              <BarList
                rows={chartRows}
                valueKey={metric}
                formatValue={(v) => activeMetric.format(v, cur)}
                secondary={{ key: "spend", label: "Spend", format: (v) => money(v, cur) }}
                color={metric === "costPerResult" ? "var(--serious)" : "var(--series-1)"}
                emphasisId={metric === "costPerResult" ? worst?.id : null}
                emptyMessage="No data for this breakdown"
              />
            </div>
          )}

          {metric === "costPerResult" && best && worst && best.id !== worst.id && (
            <div className="card card-pad" style={{ fontSize: 13, color: "var(--text-secondary)" }}>
              <strong style={{ color: "var(--text)" }}>{worst.key}</strong> costs{" "}
              <strong className="tnum" style={{ color: "var(--serious)" }}>
                {money(worst.costPerResult, cur)}
              </strong>{" "}
              per result versus{" "}
              <strong className="tnum" style={{ color: "var(--good)" }}>
                {money(best.costPerResult, cur)}
              </strong>{" "}
              for <strong style={{ color: "var(--text)" }}>{best.key}</strong> —{" "}
              {(worst.costPerResult / (best.costPerResult || 1)).toFixed(1)}x the cost for the same
              outcome.
            </div>
          )}

          <div className="card" style={{ overflowX: "auto" }}>
            <table style={{ minWidth: 720 }}>
              <thead>
                <tr>
                  <th className="no-sort">{titleCase(cut)}</th>
                  <th className="no-sort num">Spend</th>
                  <th className="no-sort num">Results</th>
                  <th className="no-sort num">Cost/result</th>
                  <th className="no-sort num">Impressions</th>
                  <th className="no-sort num">CTR</th>
                  <th className="no-sort num">CPM</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.key}>
                    <td>{row.key}</td>
                    <td className="num">{money(row.spend, cur)}</td>
                    <td className="num">{count(row.results)}</td>
                    <td className="num">
                      {row.results > 0 ? money(row.costPerResult, cur) : "—"}
                    </td>
                    <td className="num">{count(row.impressions, { compact: true })}</td>
                    <td className="num">{percent(row.ctr)}</td>
                    <td className="num">{money(row.cpm, cur)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length === 0 && <div className="empty">No rows for this breakdown.</div>}
          </div>
        </>
      )}
    </div>
  );
}
