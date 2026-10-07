import { useMemo } from "react";
import { Link } from "react-router-dom";
import {
  Banknote,
  Eye,
  Gauge,
  MousePointerClick,
  Repeat,
  Sparkles,
  Target,
} from "lucide-react";
import { useAudit } from "../App.jsx";
import Skeleton from "../components/Skeleton.jsx";
import StatTile from "../charts/StatTile.jsx";
import TimeSeries from "../charts/TimeSeries.jsx";
import BarList from "../charts/BarList.jsx";
import Funnel from "../charts/Funnel.jsx";
import { count, decimal, delta, money, percent, SEVERITY_COLOR, SEVERITY_ORDER } from "../lib/format.js";
import { useT } from "../lib/i18n.jsx";

export default function Overview() {
  const { data, search } = useAudit();
  const t = useT();

  // Every hook runs before the early return below: bailing out above a useMemo
  // changes the hook count between renders, and React throws as soon as the
  // audit payload lands.
  const campaigns = data?.campaigns ?? [];

  const topCampaigns = useMemo(
    () =>
      [...campaigns]
        .filter((c) => c.metrics.spend > 0)
        .sort((a, b) => b.metrics.spend - a.metrics.spend)
        .slice(0, 8)
        .map((c) => ({ id: c.id, key: c.name, spend: c.metrics.spend, cpr: c.metrics.costPerResult })),
    [campaigns]
  );

  // Cost per result by campaign, worst first -- the ranking people act on.
  const worstCampaigns = useMemo(
    () =>
      [...campaigns]
        .filter((c) => c.metrics.results > 0 && c.metrics.spend > 5)
        .sort((a, b) => b.metrics.costPerResult - a.metrics.costPerResult)
        .slice(0, 8)
        .map((c) => ({
          id: c.id,
          key: c.name,
          cpr: c.metrics.costPerResult,
          results: c.metrics.results,
        })),
    [campaigns]
  );

  if (!data) return <Skeleton />;

  const { summary, audit, trend, account } = data;
  const cur = account.currency;
  const m = summary.metrics;
  const prev = summary.previousMetrics || {};
  const spendSeries = trend.map((d) => d.spend);
  const resultSeries = trend.map((d) => d.results);
  const resultLabel = t(m.resultLabel);
  const worstId = worstCampaigns[0]?.id;

  const funnelStages = [
    { label: t("Impressions"), value: m.impressions },
    { label: t("Link clicks"), value: m.linkClicks },
    { label: t("Landing page views"), value: m.landingPageViews },
    { label: resultLabel, value: m.results },
  ];

  // The one finding worth reading if you read nothing else.
  const headline = [...audit.findings].sort((a, b) => (b.impact || 0) - (a.impact || 0))[0];

  const scoreColor =
    audit.score >= 75 ? "var(--good)" : audit.score >= 50 ? "var(--warning)" : "var(--critical)";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Hero: the one number this view leads with, plus severity split. */}
      <section
        className="grid"
        style={{ gridTemplateColumns: "minmax(260px, 320px) 1fr", alignItems: "stretch" }}
      >
        <div className="card card-pad">
          <div style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 500 }}>
            {t("Account health")}
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginTop: 4 }}>
            <span style={{ fontSize: 52, fontWeight: 700, letterSpacing: "-0.03em", color: scoreColor, lineHeight: 1 }}>
              {audit.score}
            </span>
            <span style={{ fontSize: 15, color: "var(--text-muted)" }}>/ 100</span>
          </div>
          <div style={{ marginTop: 6, fontWeight: 600 }}>
            {audit.grade.letter} · {t(audit.grade.label)}
          </div>

          <div style={{ marginTop: 14, display: "flex", flexWrap: "wrap", gap: 8 }}>
            {SEVERITY_ORDER.filter((s) => audit.counts[s] > 0).map((s) => (
              <Link
                key={s}
                to={{ pathname: "findings", search: `${search}&severity=${s}` }}
                className="tag"
                style={{ background: "var(--surface-sunken)", textTransform: "capitalize" }}
              >
                <span className="dot" style={{ background: SEVERITY_COLOR[s] }} />
                {audit.counts[s]} {t(s)}
              </Link>
            ))}
          </div>

          {audit.estimatedWaste > 0 && (
            <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--border)" }}>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{t("Recoverable spend")}</div>
              <div style={{ fontSize: 20, fontWeight: 600, color: "var(--serious)" }}>
                {money(audit.estimatedWaste, cur)}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                {t("{pct} of spend this window", { pct: percent((audit.estimatedWaste / (m.spend || 1)) * 100, 0) })}
              </div>
            </div>
          )}
        </div>

        <div
          className="grid"
          // 235px floor lands three across beside the score card, so six tiles
          // fill two even rows instead of wrapping 4 + 2 with a gap.
          style={{ gridTemplateColumns: "repeat(auto-fit, minmax(235px, 1fr))", gap: 12 }}
        >
          <StatTile
            label={t("Spend")}
            value={money(m.spend, cur)}
            delta={delta(m.spend, prev.spend)}
            series={spendSeries}
            Icon={Banknote}
            tint="var(--series-1)"
          />
          <StatTile
            label={resultLabel}
            value={count(m.results)}
            delta={delta(m.results, prev.results)}
            sub={`${t("via")} ${m.resultActionType || "n/a"}`}
            series={resultSeries}
            Icon={Target}
            tint="var(--series-3)"
            accent="var(--series-3)"
          />
          <StatTile
            label={t("Cost per result")}
            value={money(m.costPerResult, cur)}
            delta={delta(m.costPerResult, prev.costPerResult, { lowerIsBetter: true })}
            sub={prev.costPerResult ? t("vs {v} last period", { v: money(prev.costPerResult, cur) }) : undefined}
            Icon={Gauge}
            tint="var(--series-2)"
          />
          <StatTile
            label="CTR"
            value={percent(m.ctr)}
            delta={delta(m.ctr, prev.ctr)}
            sub={t("{n} clicks", { n: count(m.clicks) })}
            Icon={MousePointerClick}
            tint="var(--series-1)"
          />
          <StatTile
            label="CPM"
            value={money(m.cpm, cur)}
            delta={delta(m.cpm, prev.cpm, { lowerIsBetter: true })}
            sub={t("{n} impressions", { n: count(m.impressions, { compact: true }) })}
            Icon={Eye}
            tint="var(--series-2)"
          />
          <StatTile
            label={t("Frequency")}
            value={decimal(m.frequency)}
            delta={delta(m.frequency, prev.frequency, { lowerIsBetter: true })}
            sub={t("{n} reached", { n: count(m.reach, { compact: true }) })}
            Icon={Repeat}
            tint="var(--series-3)"
            accent="var(--series-3)"
          />
        </div>
      </section>

      {/* Two measures, two charts -- never one plot with two y-scales. */}
      <section className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
        <div className="card card-pad">
          <div className="card-head">
            <span className="card-title">{t("Spend per day")}</span>
            <span className="card-sub">{t("{n} days", { n: trend.length })}</span>
          </div>
          <TimeSeries
            data={trend}
            yKey="spend"
            formatValue={(v, o) => (o?.axis ? money(v, cur, { compact: true }) : money(v, cur))}
            emptyMessage={t("No daily spend in this window")}
          />
        </div>

        <div className="card card-pad">
          <div className="card-head">
            <span className="card-title">{t("{label} per day", { label: resultLabel })}</span>
            <span className="card-sub">{t("cost per result")}: {money(m.costPerResult, cur)}</span>
          </div>
          <TimeSeries
            data={trend}
            yKey="results"
            color="var(--series-3)"
            formatValue={(v) => count(v)}
            emptyMessage={t("No results recorded in this window")}
          />
        </div>
      </section>

      {headline && headline.impact > 0 && (
        <section className="insight">
          <div className="insight-glow" aria-hidden="true" />
          <div className="insight-body">
            <span className="insight-tag">
              <Sparkles size={13} />
              {t("Biggest opportunity")}
            </span>
            <div className="insight-amount">{money(headline.impact, cur)}</div>
            <p className="insight-text">
              <strong>{headline.title}</strong> — {headline.detail}
            </p>
            <Link to={{ pathname: "findings", search }} className="insight-link">
              {t("Review in audit →")}
            </Link>
          </div>
        </section>
      )}

      <section className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
        <div className="card card-pad">
          <div className="card-head">
            <span className="card-title">{t("Conversion funnel")}</span>
            <span className="card-sub">{t("whole account")}</span>
          </div>
          <Funnel stages={funnelStages} emptyMessage={t("No delivery in this window")} />
        </div>

        <div className="card card-pad">
          <div className="card-head">
            <span className="card-title">{t("Spend by campaign")}</span>
            <Link to={{ pathname: "campaigns", search }} className="card-sub" style={{ color: "var(--accent)" }}>
              {t("View all →")}
            </Link>
          </div>
          <BarList
            rows={topCampaigns}
            valueKey="spend"
            formatValue={(v) => money(v, cur)}
            secondary={{ key: "cpr", label: t("Cost per result"), format: (v) => money(v, cur) }}
            emptyMessage={t("No campaign spend in this window")}
          />
        </div>
      </section>

      <section className="card card-pad">
        <div className="card-head">
          <span className="card-title">{t("Most expensive cost per result")}</span>
          <span className="card-sub">{t("worst performer highlighted · others in grey")}</span>
        </div>
        <BarList
          rows={worstCampaigns}
          valueKey="cpr"
          formatValue={(v) => money(v, cur)}
          secondary={{ key: "results", label: t("Results"), format: (v) => count(v) }}
          emphasisId={worstId}
          color="var(--serious)"
          emptyMessage={t("No converting campaigns in this window")}
        />
      </section>
    </div>
  );
}
