"""Hourly-bar helpers for the exit scan: settle what a daily bar cannot show.

A daily bar gives one open, high, low and close. It cannot say WHICH of two
levels the price reached first, nor what happened in the rest of the day a
position was opened on. Both gaps are filled here from 1-hour bars, which
yfinance keeps for about two years:

* A day whose range holds BOTH the stop and TP1 is ambiguous. The hourly bars
  of that day usually show which level was touched first (`first_touch`).
* The remainder of the ENTRY day, after the position existed, is checked hour
  by hour. The daily walk skips the entry bar on purpose (its pre-entry low
  must not stop out a position that did not exist yet), which left a stop or
  target touched later the same day unseen, and a missed stop only ever deletes
  losses.

Everything here is pure (frames in, answers out) so it is easy to test; the
engine owns fetching, fills and closing. The honesty rules are the engine's own,
one resolution down: when two levels sit in the same hourly bar the stop is taken,
and when the data is missing, inconsistent or not really hourly, the answer is
"unknown" and the caller keeps the cautious daily rule.

Time conventions: hourly timestamps from yfinance are tz-aware (America/New_York
for US equities, UTC for crypto) while daily ones are not, so every timestamp is
converted to naive UTC here (a naive input is read as UTC, like every stored time
in the app). A bar's "day" is its calendar date in the instrument's own market
time: New York for a US equity, UTC for a crypto pair, the same reading the daily
bars use.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from app.markets import US_MARKET_TZ, is_always_on, us_session_bounds

logger = logging.getLogger(__name__)

HOURLY_INTERVAL = "1h"
HOURLY_BAR = timedelta(hours=1)

# yfinance accepts these periods for 1h bars (it refuses anything beyond ~730
# days; the ladder stops at one year to stay clear of that edge). Each period is
# paired with the calendar days it is sure to cover, so the shortest one that
# reaches back far enough is requested. "5d" is five TRADING days, which is at
# least five calendar days, hence 4 with a day of margin.
HOURLY_PERIODS: tuple[tuple[str, int], ...] = (
    ("5d", 4),
    ("1mo", 28),
    ("3mo", 85),
    ("6mo", 175),
    ("1y", 350),
)

# A real hourly frame has a median gap between bars of one hour (the overnight
# gap is a minority of the rows). A daily frame's is a day or more. Anything
# above this is not hourly data, whatever the caller asked for: a provider that
# ignores `interval` and hands back daily bars must not be walked as if it were
# hourly, or a whole day's range would pose as one hour.
MAX_MEDIAN_BAR_GAP = timedelta(hours=2)

# After an entry day has ended, its last hourly bar may be slow to appear. The
# entry day counts as fully checked once the final bar is in, or once this many
# days have passed since its close (then the data is not coming, and re-asking
# on every tick forever would be waste).
ENTRY_DAY_DATA_GRACE = timedelta(days=1)

# How a position's exit was placed (PaperPosition.exit_resolution).
RESOLUTION_DAILY = "daily"  # one level on the daily bar, or the open already settled the order
RESOLUTION_HOURLY = "hourly"  # located on an hourly bar
RESOLUTION_DAILY_AMBIGUOUS = "daily_ambiguous_stop_first"  # both levels in a daily bar, no hourly answer: stop
RESOLUTION_HOURLY_AMBIGUOUS = "hourly_ambiguous_stop_first"  # both levels in ONE hourly bar: stop

# How the rest of a position's entry day was checked (PaperPosition.entry_day_check).
ENTRY_DAY_HOURLY = "hourly"  # every hour after the entry was checked
ENTRY_DAY_DAILY_ONLY = "daily_only"  # hourly bars never came (or were incomplete): the entry day stays unchecked


def market_tz(symbol: str):
    """The clock that defines a bar's day for `symbol`."""
    return timezone.utc if is_always_on(symbol) else US_MARKET_TZ


def market_day_of(symbol: str, moment: datetime) -> date:
    """The calendar date of `moment` (naive UTC, or tz-aware) in `symbol`'s market time."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(market_tz(symbol)).date()


def hourly_period_for(day: date, today: date) -> str | None:
    """The shortest history period that reaches back to `day`, or None when it is
    older than the hourly history reliably goes."""
    age_days = (today - day).days
    for period, covers_days in HOURLY_PERIODS:
        if age_days <= covers_days:
            return period
    return None


def day_end(symbol: str, day: date) -> datetime | None:
    """When `day` ends for `symbol` as naive UTC: the session close for a US
    equity (1:00 pm on an early-close day), midnight UTC for a crypto pair. None
    for an equity date with no session at all."""
    if is_always_on(symbol):
        return datetime.combine(day + timedelta(days=1), datetime.min.time())
    bounds = us_session_bounds(day)
    if bounds is None:
        return None
    return bounds.close.astimezone(timezone.utc).replace(tzinfo=None)


def prepare_hourly(frame: pd.DataFrame | None, symbol: str) -> pd.DataFrame | None:
    """`frame` (a provider's `get_ohlcv(..., interval="1h")` result) as a tidy
    frame with `start` (naive UTC), `day`, `open`, `high`, `low`, `close`, in time
    order, or None when it can't honestly be used as hourly bars.

    None covers: nothing there, a missing column, unreadable dates, fewer than
    two bars, and data whose bars are not about an hour apart (see
    MAX_MEDIAN_BAR_GAP)."""
    if frame is None or frame.empty or not {"date", "open", "high", "low"} <= set(frame.columns):
        return None
    starts = pd.to_datetime(frame["date"], utc=True, errors="coerce")
    out = pd.DataFrame(
        {
            "start": starts,
            "open": pd.to_numeric(frame["open"], errors="coerce"),
            "high": pd.to_numeric(frame["high"], errors="coerce"),
            "low": pd.to_numeric(frame["low"], errors="coerce"),
            "close": pd.to_numeric(frame["close"], errors="coerce") if "close" in frame.columns else float("nan"),
        }
    )
    out = out.dropna(subset=["start", "open", "high", "low"])
    out = out.sort_values("start").drop_duplicates(subset="start", keep="last").reset_index(drop=True)
    if len(out) < 2:
        return None
    if out["start"].diff().dropna().median() > MAX_MEDIAN_BAR_GAP:
        return None
    out["day"] = out["start"].dt.tz_convert(market_tz(symbol)).dt.date
    out["start"] = out["start"].dt.tz_localize(None)
    return out


def levels_touched(direction: str, stop: float, tp1: float, high: float, low: float) -> tuple[bool, bool]:
    """(stop touched, TP1 touched) by a bar with this high and low."""
    if direction == "long":
        return low <= stop, high >= tp1
    return high >= stop, low <= tp1


def confirms_daily_range(direction: str, stop: float, tp1: float, hours: pd.DataFrame) -> bool:
    """Whether `hours` (one day's hourly bars) reach BOTH levels, as the daily bar
    did. If an hour is missing from the data, the level it held may be the very
    first thing that happened, so an hourly order built on a partial day is not
    trusted."""
    if hours.empty:
        return False
    stop_touched, tp_touched = levels_touched(direction, stop, tp1, float(hours["high"].max()), float(hours["low"].min()))
    return stop_touched and tp_touched


@dataclass(frozen=True)
class HourlyTouch:
    row: int  # which row of the frame that was walked
    reason: str  # "stop_hit" | "tp1_hit"
    both: bool  # both levels sat inside this one hour: the stop is taken
    in_entry_hour: bool  # the bar the position was opened in


def first_touch(
    direction: str, stop: float, tp1: float, hours: pd.DataFrame, *, entry_row: int | None = None
) -> HourlyTouch | None:
    """The first hourly bar, in order, that touches the stop or TP1, or None.

    Within one hour the stop is checked before TP1: an hourly bar cannot order its
    own high and low any better than a daily one can, so the unfavourable branch
    is taken, the same rule one level down.

    `entry_row` is the bar the position was opened in, which is partly BEFORE the
    entry. Only the stop can fire in it, never the target: a stop touched there
    may have been touched before the position existed (rare: it costs a trade
    that never was exposed, which only ever understates results), but a target
    touched there could have been touched before too, and crediting a win the
    position never earned is the flattering direction."""
    for row, (high, low) in enumerate(zip(hours["high"], hours["low"], strict=True)):
        stop_touched, tp_touched = levels_touched(direction, stop, tp1, float(high), float(low))
        if row == entry_row:
            if stop_touched:
                return HourlyTouch(row, "stop_hit", both=False, in_entry_hour=True)
            continue
        if stop_touched:
            return HourlyTouch(row, "stop_hit", both=tp_touched, in_entry_hour=False)
        if tp_touched:
            return HourlyTouch(row, "tp1_hit", both=False, in_entry_hour=False)
    return None


def entry_hour_row(hours: pd.DataFrame, opened_at: datetime) -> int | None:
    """Row of the hourly bar that was already under way when the position opened
    (it started before `opened_at` and ends after it), or None. A position opened
    exactly on a bar's start has no such bar: that bar is wholly after the entry."""
    for row, start in enumerate(hours["start"]):
        if start < opened_at < start + HOURLY_BAR:
            return row
    return None


def bars_after_entry(hours: pd.DataFrame, opened_at: datetime) -> pd.DataFrame:
    """The hourly bars the position could have been exposed to: the bar it was
    opened in (see first_touch for how that one is limited) and every later bar."""
    return hours[hours["start"] + HOURLY_BAR > opened_at].reset_index(drop=True)


class HourlyBars:
    """One symbol's hourly history for one exit sweep.

    Fetches lazily, at most once per history length, and remembers a failure so a
    second position on the same symbol (or a second ambiguous day) does not ask
    again within the sweep. `fetch(period)` returns the provider's raw frame and
    may raise; every failure is the same answer here: no hourly data, and the
    caller keeps the cautious daily rule. `now` (naive UTC) cuts off bars that
    start after the engine's own clock, so a simulated clock never sees the future."""

    def __init__(self, symbol: str, now: datetime, fetch: Callable[[str], pd.DataFrame]):
        self._symbol = symbol
        self._now = now
        self._fetch = fetch
        self._today = market_day_of(symbol, now)
        self._rank = -1  # index in HOURLY_PERIODS of the longest period fetched so far
        self._frame: pd.DataFrame | None = None

    def day_rows(self, day: date) -> pd.DataFrame | None:
        """All hourly bars of `day` (market time) that exist as of now, in order,
        or None when there are none to use: too old, not fetched, not hourly data."""
        period = hourly_period_for(day, self._today)
        if period is None:
            return None
        rank = [name for name, _ in HOURLY_PERIODS].index(period)
        if rank > self._rank:
            self._rank = rank
            try:
                self._frame = prepare_hourly(self._fetch(period), self._symbol)
            except Exception as exc:  # noqa: BLE001 - AllProvidersFailedError or any provider surprise: no hourly data
                logger.info("No hourly bars for %s (%s): %s", self._symbol, period, exc)
                self._frame = None
        if self._frame is None:
            return None
        rows = self._frame[(self._frame["day"] == day) & (self._frame["start"] <= self._now)]
        return rows.reset_index(drop=True) if not rows.empty else None
