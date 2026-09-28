import { useMemo, useState } from "react";
import { useAudit } from "../App.jsx";
import Skeleton from "../components/Skeleton.jsx";
import { count, decimal, money, percent, titleCase } from "../lib/format.js";
import { Image, Layers, Megaphone } from "lucide-react";

const LEVEL_ICON = { campaigns: Megaphone, adsets: Layers, ads: Image };

/**
 * Sortable, filterable table across campaigns / ad sets / ads.
 *
 * This is also the table view the charts lean on: every value a tooltip shows
 * elsewhere is readable here, so no number is gated behind a hover.
 */

const LEVELS = [
  { key: "campaigns", label: "Campaigns" },
  { key: "adsets", label: "Ad sets" },
  { key: "ads", label: "Ads" },
];

const COLUMNS = [
  { key: "name", label: "Name", sort: (r) => r.name?.toLowerCase() || "", align: "left" },
  { key: "spend", label: "Spend", sort: (r) => r.metrics.spend },
  { key: "results", label: "Results", sort: (r) => r.metrics.results },
  { key: "costPerResult", label: "Cost/result", sort: (r) => r.metrics.costPerResult },
  { key: "impressions", label: "Impr.", sort: (r) => r.metrics.impressions },
  { key: "ctr", label: "CTR", sort: (r) => r.metrics.ctr },
  { key: "cpc", label: "CPC", sort: (r) => r.metrics.cpc },
  { key: "cpm", label: "CPM", sort: (r) => r.metrics.cpm },
  { key: "frequency", label: "Freq.", sort: (r) => r.metrics.frequency },
];

export default function Campaigns() {
  const { data } = useAudit();
  const [level, setLevel] = useState("campaigns");
  const [status, setStatus] = useState("all");
  const [objective, setObjective] = useState("all");
  const [query, setQuery] = useState("");
  const [minSpend, setMinSpend] = useState(0);
  const [sort, setSort] = useState({ key: "spend", dir: "desc" });

  // Hooks first -- see Overview for why nothing may return above them.
  const rows = data?.[level] ?? [];

  const objectives = useMemo(
    () => ["all", ...new Set((data?.campaigns ?? []).map((c) => c.objective).filter(Boolean))],
    [data]
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = rows.filter((r) => {
      if (status !== "all" && r.status !== status) return false;
      if (objective !== "all" && r.objective !== objective) return false;
      if (r.metrics.spend < minSpend) return false;
      if (needle && !r.name?.toLowerCase().includes(needle)) return false;
      return true;
    });

    const column = COLUMNS.find((c) => c.key === sort.key) || COLUMNS[1];
    return [...list].sort((a, b) => {
      const av = column.sort(a);
      const bv = column.sort(b);
      const cmp = typeof av === "string" ? av.localeCompare(bv) : av - bv;
      return sort.dir === "asc" ? cmp : -cmp;
    });
  }, [rows, status, objective, minSpend, query, sort]);

  const totals = useMemo(
    () =>
      filtered.reduce(
        (acc, r) => ({
          spend: acc.spend + r.metrics.spend,
          results: acc.results + r.metrics.results,
          impressions: acc.impressions + r.metrics.impressions,
          clicks: acc.clicks + r.metrics.clicks,
        }),
        { spend: 0, results: 0, impressions: 0, clicks: 0 }
      ),
    [filtered]
  );

  if (!data) return <Skeleton />;
  const cur = data.account.currency;

  // Median cost per result across the visible rows -- the pill colours are
  // relative to this account, not an invented industry benchmark.
  const median = (() => {
    const vals = filtered
      .filter((r) => r.metrics.results > 0)
      .map((r) => r.metrics.costPerResult)
      .sort((a, b) => a - b);
    if (!vals.length) return 0;
    const mid = Math.floor(vals.length / 2);
    return vals.length % 2 ? vals[mid] : (vals[mid - 1] + vals[mid]) / 2;
  })();

  function toggleSort(key) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="card card-pad" style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <div style={{ display: "flex", gap: 6 }}>
          {LEVELS.map((l) => (
            <button
              key={l.key}
              className={`chip ${level === l.key ? "active" : ""}`}
              onClick={() => setLevel(l.key)}
            >
              {l.label} {(data[l.key] || []).length}
            </button>
          ))}
        </div>

        <input
          className="input"
          placeholder="Search by name…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ minWidth: 180, flex: 1 }}
        />

        <select className="select" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="all">Any status</option>
          <option value="ACTIVE">Active</option>
          <option value="PAUSED">Paused</option>
          <option value="ARCHIVED">Archived</option>
        </select>

        {level !== "ads" && (
          <select className="select" value={objective} onChange={(e) => setObjective(e.target.value)}>
            {objectives.map((o) => (
              <option key={o} value={o}>
                {o === "all" ? "Any objective" : titleCase(o.replace("OUTCOME_", ""))}
              </option>
            ))}
          </select>
        )}

        <label style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12, color: "var(--text-muted)" }}>
          Min spend
          <input
            className="input"
            type="number"
            min="0"
            value={minSpend}
            onChange={(e) => setMinSpend(Number(e.target.value) || 0)}
            style={{ width: 84 }}
          />
        </label>
      </div>

      <div className="card" style={{ overflowX: "auto" }}>
        <table style={{ minWidth: 900 }}>
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  onClick={() => toggleSort(c.key)}
                  className={c.align === "left" ? "" : "num"}
                  style={{ textAlign: c.align === "left" ? "left" : "right" }}
                >
                  {c.label}
                  {sort.key === c.key && <span style={{ marginLeft: 4 }}>{sort.dir === "asc" ? "↑" : "↓"}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((row) => {
              const LeadIcon = LEVEL_ICON[level] || Megaphone;
              return (
              <tr key={row.id}>
                <td style={{ maxWidth: 360 }}>
                  <div className="cell-lead">
                    <span
                      className="cell-chip"
                      style={
                        row.status === "ACTIVE"
                          ? { background: "var(--tint-green)", color: "#0a7c3f" }
                          : undefined
                      }
                      title={row.status}
                    >
                      <LeadIcon size={14} />
                    </span>
                    <span className="cell-text">
                      <span className="cell-name" title={row.name}>
                        {row.name}
                      </span>
                      <span className="cell-meta">
                        {row.objective
                          ? titleCase(row.objective.replace("OUTCOME_", ""))
                          : titleCase(row.status || "")}
                        {row.learningStage === "LEARNING_LIMITED" && (
                          <span style={{ color: "var(--serious)", fontWeight: 600 }}>
                            {" "}· learning limited
                          </span>
                        )}
                      </span>
                    </span>
                  </div>
                </td>
                <td className="num">{money(row.metrics.spend, cur)}</td>
                <td className="num">{count(row.metrics.results)}</td>
                <td className="num">
                  {row.metrics.results > 0 ? (
                    <span
                      className={`pill ${
                        median > 0 && row.metrics.costPerResult > median * 1.5
                          ? "pill-bad"
                          : median > 0 && row.metrics.costPerResult <= median
                            ? "pill-good"
                            : "pill-neutral"
                      }`}
                      title={median > 0 ? `Account median ${money(median, cur)}` : undefined}
                    >
                      {money(row.metrics.costPerResult, cur)}
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="num">{count(row.metrics.impressions, { compact: true })}</td>
                <td className="num">{percent(row.metrics.ctr)}</td>
                <td className="num">{money(row.metrics.cpc, cur)}</td>
                <td className="num">{money(row.metrics.cpm, cur)}</td>
                <td className="num">{decimal(row.metrics.frequency)}</td>
              </tr>
              );
            })}
          </tbody>
          {filtered.length > 0 && (
            <tfoot>
              <tr style={{ background: "var(--surface-2)", fontWeight: 600 }}>
                <td>{filtered.length} rows</td>
                <td className="num">{money(totals.spend, cur)}</td>
                <td className="num">{count(totals.results)}</td>
                <td className="num">
                  {totals.results > 0 ? money(totals.spend / totals.results, cur) : "—"}
                </td>
                <td className="num">{count(totals.impressions, { compact: true })}</td>
                <td className="num">
                  {totals.impressions > 0
                    ? percent((totals.clicks / totals.impressions) * 100)
                    : "—"}
                </td>
                <td className="num" colSpan={3} />
              </tr>
            </tfoot>
          )}
        </table>
        {filtered.length === 0 && <div className="empty">Nothing matches these filters.</div>}
      </div>
    </div>
  );
}
