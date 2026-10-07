import { useT } from "../lib/i18n.jsx";
/**
 * The last hand-rolled chart bits.
 *
 * Everything with axes now runs on Recharts; what remains is the empty state,
 * shared by every chart so "no data in this window" looks the same everywhere
 * rather than each chart inventing its own blank.
 */

export function ChartEmpty({ message }) {
  const t = useT();
  message = message || t("No data in this window");
  return (
    <div className="chart-empty">
      <span>{message}</span>
    </div>
  );
}
