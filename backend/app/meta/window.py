"""
Reporting window handling.

Meta takes a window in one of two mutually exclusive shapes: a named
``date_preset`` ("last_30d") or an explicit ``time_range`` ({"since","until"}).
Sending both is an error, so every call site takes one resolved dict of Graph
params rather than a preset string it has to translate itself.

The previous-period comparison needs the window's *length*, which a preset name
alone does not give you -- so that is resolved here too, in one place, instead
of being re-derived wherever a delta is needed.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

# Days each preset covers. Used to build the matching previous period and to
# label custom ranges consistently.
PRESET_DAYS = {
    "today": 1,
    "yesterday": 1,
    "last_3d": 3,
    "last_7d": 7,
    "last_14d": 14,
    "last_28d": 28,
    "last_30d": 30,
    "last_90d": 90,
}

VALID_PRESETS = set(PRESET_DAYS) | {
    "this_week_mon_today",
    "this_month",
    "last_month",
    "this_quarter",
    "maximum",
}


@dataclass(frozen=True)
class Window:
    """A resolved reporting window."""

    params: dict          # what Graph receives: date_preset OR time_range
    key: str              # stable cache key
    label: str            # human label for the UI
    since: date | None    # resolved bounds, when knowable
    until: date | None

    @property
    def days(self) -> int | None:
        if self.since and self.until:
            return (self.until - self.since).days + 1
        return None

    def previous(self) -> dict | None:
        """Graph params for the equal-length window immediately before this one.

        Meta has no "previous period" preset, so the range is built by hand.
        Returns None when the window has no knowable length (``maximum``), in
        which case the UI simply shows no deltas rather than inventing them.
        """
        days = self.days
        if not days or not self.since:
            return None
        prev_until = self.since - timedelta(days=1)
        prev_since = prev_until - timedelta(days=days - 1)
        return {"time_range": {"since": prev_since.isoformat(), "until": prev_until.isoformat()}}


def _preset_bounds(preset: str, today: date) -> tuple[date | None, date | None]:
    """Resolve a preset to concrete dates, matching how Meta reports them.

    Rolling presets exclude today: Meta's "last 7 days" means the 7 complete
    days ending yesterday. Getting this wrong shifts every previous-period
    delta by a day.
    """
    if preset == "today":
        return today, today
    if preset == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    if preset in PRESET_DAYS:
        until = today - timedelta(days=1)
        return until - timedelta(days=PRESET_DAYS[preset] - 1), until
    if preset == "this_month":
        return today.replace(day=1), today
    if preset == "last_month":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    if preset == "this_week_mon_today":
        return today - timedelta(days=today.weekday()), today
    if preset == "this_quarter":
        start_month = 3 * ((today.month - 1) // 3) + 1
        return today.replace(month=start_month, day=1), today
    return None, None  # maximum, or anything unrecognised


PRESET_LABELS = {
    "today": "Today",
    "yesterday": "Yesterday",
    "last_3d": "Last 3 days",
    "last_7d": "Last 7 days",
    "last_14d": "Last 14 days",
    "last_28d": "Last 28 days",
    "last_30d": "Last 30 days",
    "last_90d": "Last 90 days",
    "this_week_mon_today": "This week",
    "this_month": "This month",
    "last_month": "Last month",
    "this_quarter": "This quarter",
    "maximum": "Lifetime",
}


def resolve_window(
    preset: str | None = None,
    since: str | None = None,
    until: str | None = None,
    *,
    today: date | None = None,
) -> Window:
    """Build a Window from query params. An explicit range wins over a preset."""
    today = today or date.today()

    if since and until:
        try:
            start = date.fromisoformat(since)
            end = date.fromisoformat(until)
        except ValueError as cause:
            raise ValueError("since/until must be YYYY-MM-DD") from cause
        if start > end:
            start, end = end, start
        # Meta has no data for the future; clamping avoids an empty result that
        # looks like "this campaign stopped delivering".
        end = min(end, today)
        return Window(
            params={"time_range": {"since": start.isoformat(), "until": end.isoformat()}},
            key=f"range:{start}:{end}",
            label=f"{start.isoformat()} to {end.isoformat()}",
            since=start,
            until=end,
        )

    name = preset or "last_30d"
    if name not in VALID_PRESETS:
        raise ValueError(f"Unknown preset '{name}'")
    start, end = _preset_bounds(name, today)
    return Window(
        params={"date_preset": name},
        key=f"preset:{name}",
        label=PRESET_LABELS.get(name, name),
        since=start,
        until=end,
    )
