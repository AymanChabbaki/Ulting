import { useEffect, useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { DayPicker } from "react-day-picker";
import { format, isAfter, startOfDay } from "date-fns";
import { CalendarDays, ChevronDown } from "lucide-react";

/**
 * Date window picker, in the shape Ads Manager uses: a preset list on the left,
 * two months of calendar on the right, an explicit Update so a half-picked
 * range never triggers a fetch.
 *
 * Radix Popover handles the outside-click, focus trap and Escape that this
 * previously did by hand; react-day-picker handles the calendar grid, which is
 * more locale and edge-case correct than a hand-rolled month matrix.
 *
 * The value is `{ preset }` or `{ since, until }` -- never both, because Meta
 * rejects a request carrying date_preset and time_range together.
 */

const PRESETS = [
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "last_7d", label: "Last 7 days" },
  { key: "last_14d", label: "Last 14 days" },
  { key: "last_28d", label: "Last 28 days" },
  { key: "last_30d", label: "Last 30 days" },
  { key: "last_90d", label: "Last 90 days" },
  { key: "this_week_mon_today", label: "This week" },
  { key: "this_month", label: "This month" },
  { key: "last_month", label: "Last month" },
  { key: "this_quarter", label: "This quarter" },
  { key: "maximum", label: "Lifetime" },
];

const iso = (d) => format(d, "yyyy-MM-dd");
const parse = (s) => (s ? new Date(`${s}T00:00:00`) : undefined);

export default function DateRangePicker({ value, label, onChange }) {
  const [open, setOpen] = useState(false);
  const [draftPreset, setDraftPreset] = useState(value.preset || null);
  const [range, setRange] = useState({ from: parse(value.since), to: parse(value.until) });

  const today = startOfDay(new Date());

  // Reset the draft to the committed value each time the panel opens, so an
  // abandoned half-selection cannot leak into the next open.
  useEffect(() => {
    if (!open) return;
    setDraftPreset(value.preset || null);
    setRange({ from: parse(value.since), to: parse(value.until) });
  }, [open, value.preset, value.since, value.until]);

  const canApply = Boolean(draftPreset || (range?.from && range?.to));

  function apply() {
    if (draftPreset) onChange({ preset: draftPreset });
    else if (range?.from && range?.to) onChange({ since: iso(range.from), until: iso(range.to) });
    setOpen(false);
  }

  const draftLabel = draftPreset
    ? PRESETS.find((p) => p.key === draftPreset)?.label
    : range?.from && range?.to
      ? `${iso(range.from)} → ${iso(range.to)}`
      : range?.from
        ? `${iso(range.from)} → select end date`
        : "Select a start date";

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button className="btn drp-trigger" aria-label="Change date range">
          <CalendarDays size={15} className="drp-cal-icon" />
          <span>{label}</span>
          <ChevronDown size={13} className="drp-caret" />
        </button>
      </Popover.Trigger>

      <Popover.Portal>
        <Popover.Content className="drp-panel" align="end" sideOffset={6} collisionPadding={12}>
          <div className="drp-presets">
            {PRESETS.map((p) => (
              <button
                key={p.key}
                className={`drp-preset ${draftPreset === p.key ? "active" : ""}`}
                onClick={() => {
                  setDraftPreset(p.key);
                  setRange({ from: undefined, to: undefined });
                }}
              >
                {p.label}
              </button>
            ))}
          </div>

          <div className="drp-cal">
            <DayPicker
              mode="range"
              numberOfMonths={2}
              weekStartsOn={1}
              defaultMonth={range?.from ?? new Date(today.getFullYear(), today.getMonth() - 1, 1)}
              selected={range}
              onSelect={(next) => {
                setRange(next ?? { from: undefined, to: undefined });
                // Touching the calendar means this is a custom range now.
                if (next?.from) setDraftPreset(null);
              }}
              // Meta has no data for the future; an unreachable range would
              // come back empty and read as "delivery stopped".
              disabled={(day) => isAfter(startOfDay(day), today)}
              showOutsideDays={false}
            />

            <div className="drp-foot">
              <span className="drp-draft">{draftLabel}</span>
              <span className="drp-actions">
                <Popover.Close asChild>
                  <button className="btn">Cancel</button>
                </Popover.Close>
                <button className="btn btn-primary" disabled={!canApply} onClick={apply}>
                  Update
                </button>
              </span>
            </div>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
