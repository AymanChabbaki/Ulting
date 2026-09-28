import { useId } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ChartTooltip,
  activeDot,
  axisProps,
  gridProps,
  lineProps,
  tooltipCursor,
} from "./chartTheme.jsx";
import { ChartEmpty } from "./primitives.jsx";

/**
 * Single-series time area with a crosshair.
 *
 * Deliberately one series per chart. Two measures of different scale (spend and
 * cost per result, say) go in two of these side by side rather than on one plot
 * with two y-scales -- aligning two axes invents a correlation that is not in
 * the data. A single series also needs no legend: the card title names it.
 */
export default function TimeSeries({
  data,
  xKey = "date",
  yKey,
  color = "var(--series-1)",
  formatValue = (v) => v,
  height = 200,
  emptyMessage,
}) {
  const gradientId = `grad-${useId().replace(/:/g, "")}`;
  const rows = (data || []).filter((d) => Number.isFinite(Number(d[yKey])));

  if (!rows.length) return <ChartEmpty message={emptyMessage} />;

  // Only ever three date ticks: first, middle, last. Left to itself Recharts
  // packs the axis and the labels collide at narrow card widths.
  const ticks =
    rows.length > 2
      ? [
          rows[0][xKey],
          rows[Math.floor((rows.length - 1) / 2)][xKey],
          rows[rows.length - 1][xKey],
        ]
      : rows.map((r) => r[xKey]);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={rows} margin={{ top: 12, right: 14, bottom: 0, left: 0 }}>
        <defs>
          {/* A wash, never a saturated block. */}
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.18} />
            <stop offset="100%" stopColor={color} stopOpacity={0.02} />
          </linearGradient>
        </defs>

        <CartesianGrid {...gridProps} />
        <XAxis
          dataKey={xKey}
          ticks={ticks}
          {...axisProps}
          tickFormatter={(v) => String(v).slice(5)}
          padding={{ left: 6, right: 6 }}
        />
        <YAxis {...axisProps} width={56} tickFormatter={(v) => formatValue(v, { axis: true })} />
        <Tooltip
          cursor={tooltipCursor}
          content={<ChartTooltip formatter={(v) => formatValue(v)} />}
        />
        <Area
          type="monotone"
          dataKey={yKey}
          stroke={color}
          fill={`url(#${gradientId})`}
          activeDot={activeDot(color)}
          {...lineProps}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
