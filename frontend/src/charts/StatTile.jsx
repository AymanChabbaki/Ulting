import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";

/**
 * KPI tile: label + tinted icon, hero value, delta pill, comparison line, spark.
 *
 * The value uses the font's proportional figures, not tabular-nums -- at
 * display size equal-width digits make a number like 121 look loose. Tabular
 * figures belong in columns that align vertically, which is the table view.
 *
 * Delta colour is direction x whether up is good, so a falling cost per result
 * reads green while falling results reads red. It also carries a direction
 * arrow, so the meaning never rests on colour alone.
 *
 * `tint` only ever colours the icon chip -- never the value or the delta. The
 * chip is decoration; the delta is status, and mixing the two would let a
 * brand colour impersonate a good/bad signal.
 */
export default function StatTile({
  label,
  value,
  delta,
  sub,
  series = null,
  Icon = null,
  tint = "var(--series-1)",
  accent = "var(--series-1)",
}) {
  const Arrow = delta && delta.change > 0 ? ArrowUpRight : ArrowDownRight;

  return (
    <div className="kpi">
      <div className="kpi-head">
        <span className="kpi-label">{label}</span>
        {Icon && (
          <span className="kpi-icon" style={{ "--tint": tint }}>
            <Icon size={15} />
          </span>
        )}
      </div>

      <div className="kpi-value-row">
        <span className="kpi-value">{value}</span>
        {delta && !delta.flat && (
          <span
            className={`kpi-delta ${delta.good ? "is-good" : "is-bad"}`}
            title="vs previous period of equal length"
          >
            <Arrow size={12} strokeWidth={2.4} />
            {delta.label.replace("+", "")}
          </span>
        )}
      </div>

      {sub && <div className="kpi-sub">{sub}</div>}

      {series && series.length > 1 && (
        <div className="kpi-spark">
          <Sparkline points={series} accent={accent} />
        </div>
      )}
    </div>
  );
}

function Sparkline({ points, accent }) {
  const data = points.map((v, i) => ({ i, v: Number(v) || 0 }));
  return (
    <ResponsiveContainer width="100%" height={34}>
      <AreaChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
        <Area
          type="monotone"
          dataKey="v"
          stroke={accent}
          strokeWidth={1.8}
          fill={accent}
          fillOpacity={0.12}
          dot={false}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
