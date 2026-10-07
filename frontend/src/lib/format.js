import { currentLang } from "./i18n.jsx";

// Number formats follow the interface language: 1,234.50 vs 1 234,50.
const locale = () => (currentLang() === "fr" ? "fr-FR" : "en-US");

export function money(value, currency = "USD", { compact = false } = {}) {
  const n = Number(value) || 0;
  return new Intl.NumberFormat(locale(), {
    style: "currency",
    currency,
    notation: compact && Math.abs(n) >= 10000 ? "compact" : "standard",
    maximumFractionDigits: Math.abs(n) >= 100 ? 0 : 2,
  }).format(n);
}

export function count(value, { compact = false } = {}) {
  const n = Number(value) || 0;
  return new Intl.NumberFormat(locale(), {
    notation: compact && Math.abs(n) >= 10000 ? "compact" : "standard",
    maximumFractionDigits: 0,
  }).format(n);
}

const fixed = (v, d) =>
  new Intl.NumberFormat(locale(), { minimumFractionDigits: d, maximumFractionDigits: d }).format(Number(v) || 0);
export const percent = (v, d = 2) => (currentLang() === "fr" ? `${fixed(v, d)} %` : `${fixed(v, d)}%`);
export const decimal = (v, d = 2) => fixed(v, d);

/** Meta returns budgets and lifetime spend in minor units (cents/fils). */
export const fromMinor = (v) => (Number(v) || 0) / 100;

/**
 * Percentage change, and whether that direction is good.
 * `lowerIsBetter` covers cost metrics, where a fall is the win.
 */
export function delta(current, previous, { lowerIsBetter = false } = {}) {
  const a = Number(current) || 0;
  const b = Number(previous) || 0;
  if (!b) return null;
  const change = ((a - b) / Math.abs(b)) * 100;
  const rising = change > 0;
  return {
    change,
    label: `${rising ? "+" : ""}${change.toFixed(0)}%`,
    good: lowerIsBetter ? !rising : rising,
    flat: Math.abs(change) < 0.5,
  };
}

export const SEVERITY_COLOR = {
  critical: "var(--critical)",
  high: "var(--serious)",
  medium: "var(--warning)",
  low: "var(--series-1)",
  info: "var(--de-emphasis)",
};

export const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"];

export function titleCase(value = "") {
  // Lowercase first: Meta's enums arrive SHOUTING (OUTCOME_LEADS), and a
  // capitalise-the-first-letter pass leaves "LEADS" exactly as it found it.
  return String(value)
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
