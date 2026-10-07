import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  History,
  Pause,
  Play,
  RotateCcw,
  ShieldCheck,
  X,
} from "lucide-react";

import { useAudit } from "../App.jsx";
import Skeleton from "../components/Skeleton.jsx";
import { windowQuery } from "../lib/api.js";
import { count, money, percent, titleCase } from "../lib/format.js";
import { currentLang, useT } from "../lib/i18n.jsx";

/**
 * Ad manager: the only screen in the app that writes to Meta.
 *
 * Changes are staged locally, never sent on click. They go to /ops/plan, which
 * mutates nothing and returns real before/after values, and only an explicit
 * confirm sends /ops/apply with the plan's fingerprint. If the account moved in
 * between, the server refuses rather than applying to a state nobody reviewed.
 */
export default function AdManager() {
  const { accountId, window: win, data, reload } = useAudit();
  const t = useT();

  const [budget, setBudget] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // id -> { action, value }
  const [staged, setStaged] = useState({});
  const [plan, setPlan] = useState(null);
  const [applying, setApplying] = useState(false);
  const [result, setResult] = useState(null);
  const [logOpen, setLogOpen] = useState(false);
  const [log, setLog] = useState([]);
  const [expanded, setExpanded] = useState({});

  const query = windowQuery(win);

  const load = useCallback(() => {
    setLoading(true);
    fetch(`/api/ops/budget/${accountId}?${query}`, { credentials: "same-origin" })
      .then((r) => r.json().then((j) => (r.ok ? j : Promise.reject(new Error(j?.detail?.message)))))
      .then((b) => { setBudget(b); setError(null); })
      .catch((c) => setError(c.message || t("Could not load budgets")))
      .finally(() => setLoading(false));
  }, [accountId, query]);

  useEffect(() => { load(); }, [load]);

  const changes = useMemo(
    () => Object.entries(staged).map(([id, v]) => ({ id, action: v.action, value: v.value })),
    [staged]
  );

  function stage(id, action, value) {
    setPlan(null);
    setResult(null);
    setStaged((prev) => ({ ...prev, [id]: { action, value } }));
  }

  function unstage(id) {
    setPlan(null);
    setStaged((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  async function review() {
    setError(null);
    try {
      const response = await fetch("/api/ops/plan", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_id: accountId,
          changes,
          ...Object.fromEntries(new URLSearchParams(query)),
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message || "Plan failed");
      setPlan(payload);
    } catch (cause) {
      setError(cause.message);
    }
  }

  async function confirm(dryRun) {
    if (!plan) return;
    setApplying(true);
    setError(null);
    try {
      const response = await fetch("/api/ops/apply", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_id: accountId,
          changes,
          fingerprint: plan.fingerprint,
          dry_run: dryRun,
          ...Object.fromEntries(new URLSearchParams(query)),
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message || "Apply failed");
      setResult(payload);
      if (!dryRun) {
        setStaged({});
        setPlan(null);
        load();
        reload();
      }
    } catch (cause) {
      setError(cause.message);
    } finally {
      setApplying(false);
    }
  }

  function openLog() {
    setLogOpen(true);
    fetch("/api/ops/log?limit=80", { credentials: "same-origin" })
      .then((r) => r.json())
      .then((r) => setLog(r.entries || []))
      .catch(() => {});
  }

  if (loading && !budget) return <Skeleton />;

  const cur = budget?.currency || data?.account?.currency || "USD";
  const guard = budget?.guardrails;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="warn-box">
        <ShieldCheck size={16} />
        <span>
          <strong>{t("This page writes to your live ad account.")}</strong>{" "}
          {t("Changes are staged and previewed first — nothing is sent until you confirm. Budgets cannot be raised more than {x}× in one step or set below {min}/day. Every applied change is logged.", {
            x: guard?.maxIncreaseMultiple ?? 5,
            min: money(guard?.minDailyBudget ?? 1, cur),
          })}
        </span>
      </div>

      {error && <div className="error-box">{error}</div>}

      <section className="grid" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))" }}>
        <div className="kpi">
          <div className="kpi-head"><span className="kpi-label">{t("Active daily budget")}</span></div>
          <div className="kpi-value-row">
            <span className="kpi-value">{money(budget?.activeDailyBudget || 0, cur)}</span>
          </div>
          <div className="kpi-sub">{t("across active campaigns")}</div>
        </div>
        <div className="kpi">
          <div className="kpi-head"><span className="kpi-label">{t("Spend this window")}</span></div>
          <div className="kpi-value-row">
            <span className="kpi-value">{money(budget?.accountSpend || 0, cur)}</span>
          </div>
        </div>
        <div className="kpi">
          <div className="kpi-head"><span className="kpi-label">{t("Staged changes")}</span></div>
          <div className="kpi-value-row">
            <span className="kpi-value">{changes.length}</span>
          </div>
          <div className="kpi-sub">{plan ? t("reviewed") : t("not reviewed")}</div>
        </div>
        <button className="kpi" onClick={openLog} style={{ cursor: "pointer", textAlign: "left" }}>
          <div className="kpi-head">
            <span className="kpi-label">{t("Change history")}</span>
            <span className="kpi-icon" style={{ "--tint": "var(--series-1)" }}>
              <History size={15} />
            </span>
          </div>
          <div className="kpi-sub" style={{ marginTop: 8 }}>{t("View everything this app has changed")}</div>
        </button>
      </section>

      <section className="card" style={{ overflowX: "auto" }}>
        <table style={{ minWidth: 960 }}>
          <thead>
            <tr>
              <th className="no-sort">{t("Campaign / ad set")}</th>
              <th className="no-sort num">{t("Daily budget")}</th>
              <th className="no-sort num">{t("Spend")}</th>
              <th className="no-sort num">{t("Results")}</th>
              <th className="no-sort num">{t("Cost/result")}</th>
              <th className="no-sort num">{t("Share")}</th>
              <th className="no-sort">{t("Actions")}</th>
            </tr>
          </thead>
          <tbody>
            {(budget?.campaigns || []).map((c) => (
              <Row
                key={c.id}
                row={c}
                cur={cur}
                staged={staged[c.id]}
                onStage={stage}
                onUnstage={unstage}
                expanded={!!expanded[c.id]}
                onToggle={() => setExpanded((e) => ({ ...e, [c.id]: !e[c.id] }))}
                childRows={c.adsets}
                stagedMap={staged}
              />
            ))}
          </tbody>
        </table>
        {!budget?.campaigns?.length && <div className="empty">{t("No campaigns in this account.")}</div>}
      </section>

      {changes.length > 0 && (
        <div className="ops-bar">
          <span>
            <strong>{changes.length}</strong> {changes.length === 1 ? t("change staged") : t("changes staged")}
          </span>
          <button className="btn" onClick={() => { setStaged({}); setPlan(null); setResult(null); }}>
            <RotateCcw size={14} /> {t("Discard")}
          </button>
          <button className="btn btn-primary" onClick={review}>{t("Review changes")}</button>
        </div>
      )}

      {plan && (
        <section className="card card-pad">
          <div className="card-head">
            <span className="card-title">{t("Review — nothing has been sent yet")}</span>
            <span className="card-sub">
              {t("{n} to apply", { n: plan.actionable })}
              {plan.noop > 0 && ` · ${t("{n} already in that state", { n: plan.noop })}`}
            </span>
          </div>

          <div className="plan-list">
            {plan.items.map((item) => (
              <div key={`${item.id}-${item.action}`} className={`plan-item ${item.noop ? "is-noop" : ""}`}>
                <span className="tag">{t(item.level)}</span>
                <span className="plan-name">{item.name}</span>
                <span className="plan-change">
                  <span className="plan-from">
                    {item.field === "daily_budget" ? money(item.from, cur) : item.from}
                  </span>
                  <span className="plan-arrow">→</span>
                  <span className="plan-to">
                    {item.field === "daily_budget" ? money(item.to, cur) : item.to}
                  </span>
                </span>
                {item.noop && <span className="plan-noop">{t("no change")}</span>}
              </div>
            ))}
          </div>

          <div className="studio-actions">
            <button className="btn" onClick={() => confirm(true)} disabled={applying}>
              {t("Validate with Meta (no change)")}
            </button>
            <button
              className="btn btn-primary"
              onClick={() => confirm(false)}
              disabled={applying || plan.actionable === 0}
            >
              <Check size={15} />
              {applying ? t("Applying…") : t(plan.actionable === 1 ? "Apply {n} change" : "Apply {n} changes", { n: plan.actionable })}
            </button>
            <button className="btn" onClick={() => setPlan(null)} disabled={applying}>{t("Cancel")}</button>
          </div>
        </section>
      )}

      {result && (
        <section className="card card-pad">
          <div className="card-head">
            <span className="card-title">
              {result.dryRun ? t("Validation result") : t("Applied")}
            </span>
            <span className="card-sub">
              {t("{a} ok · {f} failed · {s} skipped", { a: result.applied, f: result.failed, s: result.skipped })}
            </span>
          </div>
          {result.results.map((r) => (
            <div key={`${r.id}-${r.action}`} className="plan-item">
              {r.status === "failed" ? <AlertTriangle size={14} color="var(--critical)" />
                : <Check size={14} color="var(--good)" />}
              <span className="plan-name">{r.name}</span>
              <span className="cell-meta">{t(r.status)}{r.error ? ` — ${r.error}` : ""}</span>
            </div>
          ))}
        </section>
      )}

      {logOpen && (
        <section className="card card-pad">
          <div className="card-head">
            <span className="card-title">{t("Change history")}</span>
            <button className="btn" onClick={() => setLogOpen(false)}><X size={14} /></button>
          </div>
          {log.length === 0 ? (
            <div className="empty">{t("Nothing has been changed through this app yet.")}</div>
          ) : (
            <div className="plan-list">
              {log.map((e, i) => (
                <div key={i} className="plan-item">
                  <span className="cell-meta">{new Date(e.at).toLocaleString(currentLang() === "fr" ? "fr-FR" : "en-US")}</span>
                  <span className="tag">{t(e.level)}</span>
                  <span className="plan-name">{e.name}</span>
                  <span className="plan-change">
                    <span className="plan-from">{String(e.from)}</span>
                    <span className="plan-arrow">→</span>
                    <span className="plan-to">{String(e.to)}</span>
                  </span>
                  <span className={`cell-meta ${e.result === "failed" ? "is-bad" : ""}`}>
                    {t(e.result)}{e.dryRun ? ` (${t("dry run")})` : ""} · {e.user}
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function Row({ row, cur, staged, onStage, onUnstage, expanded, onToggle, childRows, stagedMap }) {
  const t = useT();
  const paused = row.status !== "ACTIVE";
  return (
    <>
      <tr>
        <td style={{ maxWidth: 340 }}>
          <div className="cell-lead">
            {childRows?.length > 0 && (
              <button className="icon-btn" onClick={onToggle} aria-label={t("Toggle ad sets")}>
                {expanded ? "−" : "+"}
              </button>
            )}
            <span className="cell-text">
              <span className="cell-name">{row.name}</span>
              <span className="cell-meta">
                {t(titleCase(row.status || ""))}
                {childRows?.length ? ` · ${t("{n} ad sets", { n: childRows.length })}` : ""}
              </span>
            </span>
          </div>
        </td>
        <td className="num">
          <BudgetCell row={row} cur={cur} staged={staged} onStage={onStage} />
        </td>
        <td className="num">{money(row.spend, cur)}</td>
        <td className="num">{count(row.results)}</td>
        <td className="num">{row.results > 0 ? money(row.costPerResult, cur) : "—"}</td>
        <td className="num">{percent(row.shareOfSpend, 0)}</td>
        <td>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <button
              className={`btn ${staged?.action === (paused ? "resume" : "pause") ? "btn-primary" : ""}`}
              style={{ padding: "5px 10px" }}
              onClick={() =>
                staged?.action === (paused ? "resume" : "pause")
                  ? onUnstage(row.id)
                  : onStage(row.id, paused ? "resume" : "pause")
              }
            >
              {paused ? <Play size={13} /> : <Pause size={13} />}
              {paused ? t("Resume") : t("Pause")}
            </button>
            {staged && (
              <button className="icon-btn" onClick={() => onUnstage(row.id)} title={t("Unstage")}>
                <X size={13} />
              </button>
            )}
          </div>
        </td>
      </tr>

      {expanded &&
        childRows.map((child) => (
          <tr key={child.id} className="is-child">
            <td style={{ paddingLeft: 40, maxWidth: 340 }}>
              <span className="cell-text">
                <span className="cell-name">{child.name}</span>
                <span className="cell-meta">
                  {t(titleCase(child.status || ""))}
                  {child.learningStage === "LEARNING_LIMITED" && (
                    <span style={{ color: "var(--serious)" }}> · {t("learning limited")}</span>
                  )}
                </span>
              </span>
            </td>
            <td className="num">
              <BudgetCell row={child} cur={cur} staged={stagedMap[child.id]} onStage={onStage} />
            </td>
            <td className="num">{money(child.spend, cur)}</td>
            <td className="num">{count(child.results)}</td>
            <td className="num">{child.results > 0 ? money(child.costPerResult, cur) : "—"}</td>
            <td className="num">—</td>
            <td>
              <button
                className="btn"
                style={{ padding: "5px 10px" }}
                onClick={() =>
                  onStage(child.id, child.status === "ACTIVE" ? "pause" : "resume")
                }
              >
                {child.status === "ACTIVE" ? <Pause size={13} /> : <Play size={13} />}
              </button>
            </td>
          </tr>
        ))}
    </>
  );
}

function BudgetCell({ row, cur, staged, onStage }) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(row.dailyBudget ?? "");

  if (row.dailyBudget == null) {
    return <span className="cell-meta">{row.lifetimeBudget ? t("lifetime") : "—"}</span>;
  }

  if (staged?.action === "set_daily_budget") {
    return (
      <span className="plan-change">
        <span className="plan-from">{money(row.dailyBudget, cur)}</span>
        <span className="plan-arrow">→</span>
        <span className="plan-to">{money(staged.value, cur)}</span>
      </span>
    );
  }

  if (!editing) {
    return (
      <button className="budget-btn" onClick={() => { setEditing(true); setValue(row.dailyBudget); }}>
        {money(row.dailyBudget, cur)}
      </button>
    );
  }

  return (
    <span style={{ display: "inline-flex", gap: 5 }}>
      <input
        className="input"
        type="number"
        step="0.5"
        min="1"
        value={value}
        autoFocus
        style={{ width: 90, textAlign: "right" }}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            onStage(row.id, "set_daily_budget", Number(value));
            setEditing(false);
          }
          if (e.key === "Escape") setEditing(false);
        }}
      />
      <button
        className="btn btn-primary"
        style={{ padding: "5px 9px" }}
        onClick={() => { onStage(row.id, "set_daily_budget", Number(value)); setEditing(false); }}
      >
        <Check size={13} />
      </button>
    </span>
  );
}
