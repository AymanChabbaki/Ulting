import { ChartEmpty } from "./primitives.jsx";
import { count, percent } from "../lib/format.js";

/**
 * Conversion funnel: impressions -> link clicks -> landing page views -> results.
 *
 * Not a Recharts chart: the stages are four labelled rows, and a funnel plot
 * would add axes and a legend without adding information. Bars stay
 * proportional to the top stage, which is honest -- a lead funnel really does
 * fall off a cliff after impressions -- so the step-to-step rate is labelled on
 * every row, since that is the number people act on.
 *
 * The ramp is ordinal (one hue, light->dark, validated so the lightest step
 * still clears the surface) rather than categorical: the stages are positions
 * on one path, not separate identities.
 */

const RAMP = ["var(--seq-250)", "var(--seq-350)", "var(--seq-450)", "var(--seq-550)"];

export default function Funnel({ stages, emptyMessage }) {
  const rows = (stages || []).filter((s) => Number.isFinite(Number(s.value)));
  if (!rows.length || Number(rows[0].value) <= 0) {
    return <ChartEmpty message={emptyMessage} />;
  }

  const top = Number(rows[0].value);

  return (
    <div className="funnel">
      {rows.map((stage, i) => {
        const value = Number(stage.value);
        const share = (value / top) * 100;
        const previous = i > 0 ? Number(rows[i - 1].value) : null;
        const stepRate = previous ? (value / previous) * 100 : null;

        return (
          <div
            className="funnel-stage"
            key={stage.label}
            title={`${stage.label}: ${count(value)} (${percent(share, 1)} of ${rows[0].label.toLowerCase()})`}
          >
            <div className="funnel-head">
              <span className="funnel-label">{stage.label}</span>
              <span className="funnel-nums">
                {stepRate !== null && (
                  <span className={`funnel-rate ${stepRate < 60 ? "is-low" : ""}`}>
                    {percent(stepRate, 0)} of previous
                  </span>
                )}
                <span className="funnel-value">{count(value)}</span>
              </span>
            </div>
            <div className="funnel-track">
              <div
                className="funnel-fill"
                style={{
                  width: `${Math.max(0.4, share)}%`,
                  background: RAMP[Math.min(i, RAMP.length - 1)],
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
