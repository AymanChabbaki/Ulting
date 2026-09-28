/**
 * Shared Recharts configuration.
 *
 * Recharts' defaults do not match the house style -- dashed gridlines, thin
 * 1px strokes, unbounded bar thickness, a tooltip with its own chrome. Rather
 * than re-specifying that at every call site, the props live here and each
 * chart spreads them. Anything a chart overrides is then a visible, deliberate
 * exception instead of a silent drift.
 */

export const SURFACE = "var(--surface)";

/** Horizontal hairlines only, solid -- dashing reads as "projection". */
export const gridProps = {
  stroke: "var(--grid)",
  strokeWidth: 1,
  vertical: false,
  // Recharts dashes by default; an empty string is how you turn that off.
  strokeDasharray: "",
};

export const axisProps = {
  stroke: "var(--grid)",
  tick: { fill: "var(--text-muted)", fontSize: 11 },
  tickLine: false,
  axisLine: false,
};

/** 2px line, round caps, matching the spec for every series line. */
export const lineProps = {
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  dot: false,
  // Off deliberately. Recharts replays its entry animation from zero on ANY
  // re-measure, and ResponsiveContainer re-measures whenever the container
  // changes -- collapsing the sidebar, resizing the window, even a screenshot.
  // The result is charts that blink empty during ordinary interaction. The
  // route-level fade in Shell carries the motion instead.
  isAnimationActive: false,
};

/**
 * Active/end marker: >= 8px across and ringed in the surface colour so it stays
 * legible where it crosses the line.
 */
export const activeDot = (color) => ({
  r: 5,
  fill: color,
  stroke: SURFACE,
  strokeWidth: 2,
});

/** Bars never fill their band; the leftover is air. */
export const barProps = {
  maxBarSize: 24,
  isAnimationActive: false, // see lineProps
};

/**
 * Tooltip body.
 *
 * Recharts renders its own bordered box by default; `.chart-tooltip` is the
 * same dark chip used everywhere else in the app, so hovering a chart looks
 * like hovering anything else.
 */
export function ChartTooltip({ active, payload, label, formatter, labelFormatter }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      {label != null && (
        <div className="t-label">{labelFormatter ? labelFormatter(label) : label}</div>
      )}
      {payload.map((entry, i) => (
        <div key={i} className="t-row">
          {payload.length > 1 && (
            <span className="t-swatch" style={{ background: entry.color }} />
          )}
          {payload.length > 1 && <span className="t-label">{entry.name}</span>}
          <span className="t-value">
            {formatter ? formatter(entry.value, entry) : entry.value}
          </span>
        </div>
      ))}
    </div>
  );
}

export const tooltipCursor = {
  stroke: "var(--border-strong)",
  strokeWidth: 1,
};

/** Bar/cell hover: a soft wash rather than Recharts' default grey block. */
export const barCursor = { fill: "var(--surface-sunken)", radius: 6 };
