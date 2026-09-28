import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartTooltip, axisProps, barCursor, barProps, gridProps } from "./chartTheme.jsx";
import { ChartEmpty } from "./primitives.jsx";

/**
 * Grouped columns for two or three named series (male vs female by age band).
 *
 * Categorical hues in fixed slot order, assigned by series identity -- never by
 * current rank, so filtering one out never repaints the survivors. The legend
 * is always present: with more than one series, identity must never rest on
 * colour-matching alone.
 */
export default function GroupedBars({
  groups,
  series,
  formatValue = (v) => v,
  height = 250,
  emptyMessage,
}) {
  const rows = (groups || [])
    .filter((g) => series.some((s) => Number(g.values[s.key]) > 0))
    .map((g) => ({ label: g.label, ...g.values }));

  if (!rows.length) return <ChartEmpty message={emptyMessage} />;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={rows} margin={{ top: 10, right: 8, bottom: 0, left: 0 }} barGap={2}>
        <CartesianGrid {...gridProps} />
        <XAxis dataKey="label" {...axisProps} />
        <YAxis {...axisProps} width={56} tickFormatter={(v) => formatValue(v, { axis: true })} />
        <Tooltip cursor={barCursor} content={<ChartTooltip formatter={(v) => formatValue(v)} />} />
        <Legend
          verticalAlign="bottom"
          height={28}
          iconType="circle"
          iconSize={8}
          formatter={(value) => (
            <span style={{ color: "var(--text-secondary)", fontSize: 12 }}>{value}</span>
          )}
        />
        {series.map((s) => (
          <Bar
            key={s.key}
            dataKey={s.key}
            name={s.name}
            fill={s.color}
            radius={[3, 3, 0, 0]}
            {...barProps}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
