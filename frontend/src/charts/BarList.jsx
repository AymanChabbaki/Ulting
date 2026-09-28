import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartTooltip, barCursor, barProps } from "./chartTheme.jsx";
import { ChartEmpty } from "./primitives.jsx";

/**
 * Horizontal ranked bars (spend by campaign, cost per result by placement).
 *
 * One hue for every bar. Shading each bar darker-where-bigger would
 * double-encode length as colour and burn the only free channel on information
 * the bar length already carries. `emphasisId` is the exception the form does
 * support: highlight one bar and grey the rest when the story is one row.
 */
export default function BarList({
  rows,
  labelKey = "key",
  valueKey = "spend",
  formatValue = (v) => v,
  secondary = null,
  color = "var(--series-1)",
  emphasisId = null,
  emptyMessage,
}) {
  const data = (rows || []).filter((r) => Number(r[valueKey]) > 0);
  if (!data.length) return <ChartEmpty message={emptyMessage} />;

  // Height follows the row count so bars keep a constant thickness instead of
  // stretching to fill a fixed box.
  const rowHeight = 34;
  const height = Math.max(90, data.length * rowHeight + 16);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 0, right: 62, bottom: 0, left: 0 }}
        barCategoryGap="22%"
      >
        <XAxis type="number" dataKey={valueKey} hide />
        <YAxis
          type="category"
          dataKey={labelKey}
          width={Math.min(210, Math.max(120, ...data.map((r) => String(r[labelKey]).length * 6.4)))}
          tick={{ fill: "var(--text-secondary)", fontSize: 12.5 }}
          tickLine={false}
          axisLine={false}
          interval={0}
        />
        <Tooltip
          cursor={barCursor}
          content={
            <ChartTooltip
              formatter={(value, entry) => {
                const row = entry?.payload;
                if (!secondary || !row) return formatValue(value);
                return `${formatValue(value)} · ${secondary.label} ${secondary.format(
                  row[secondary.key]
                )}`;
              }}
            />
          }
        />
        <Bar dataKey={valueKey} radius={[0, 4, 4, 0]} {...barProps}>
          {data.map((row) => {
            const dimmed =
              emphasisId && row.id !== emphasisId && row[labelKey] !== emphasisId;
            return (
              <Cell
                key={row.id || row[labelKey]}
                fill={dimmed ? "var(--de-emphasis)" : color}
              />
            );
          })}
          {/* Value at the tip -- the axis is hidden, so this carries the number. */}
          <LabelList
            dataKey={valueKey}
            position="right"
            offset={8}
            formatter={(v) => formatValue(v)}
            style={{
              fill: "var(--text)",
              fontSize: 12.5,
              fontWeight: 500,
              fontVariantNumeric: "tabular-nums",
            }}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
