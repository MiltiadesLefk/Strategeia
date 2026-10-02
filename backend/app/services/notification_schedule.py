"""When the recurring notifications are due. Pure functions with an injected clock.

The scheduler wakes every few minutes and asks these functions whether the morning note
or the weekly digest should go out now; they never touch the network or the database.
A message is due from its set time until a few hours later, so an app that was restarted
at 08:50 still sends the 08:45 note, but one started after lunch does not send a stale
"morning" note. Whether it was already sent today is the caller's `last_sent_day`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from app.markets import is_us_trading_day, to_market_time

# How long after its set time a note may still go out (catch-up after a restart).
MORNING_NOTE_CATCHUP = timedelta(hours=3)
WEEKLY_DIGEST_CATCHUP = timedelta(hours=4)

# The digest goes out after the close, once the day's bars are final.
WEEKLY_DIGEST_TIME_ET = time(16, 30)
_FRIDAY = 4


def parse_clock_time(value: str) -> time:
    """"08:45" -> time(8, 45). Raises ValueError for anything else."""
    hour_text, _, minute_text = value.partition(":")
    return time(int(hour_text), int(minute_text))


def market_day_text(now: datetime) -> str:
    """The US Eastern date of `now` (naive UTC is read as UTC), as YYYY-MM-DD."""
    return to_market_time(now).date().isoformat()


def _within(now_et: datetime, at: time, catchup: timedelta) -> bool:
    start = datetime.combine(now_et.date(), at, tzinfo=now_et.tzinfo)
    return start <= now_et < start + catchup


def morning_note_due(now: datetime, *, enabled: bool, time_et: str, last_sent_day: str | None) -> bool:
    """True when the morning note should be sent at `now`: switched on, a US trading
    day (weekends and market holidays are skipped), inside the window that starts at the
    set time, and not already sent today."""
    if not enabled:
        return False
    now_et = to_market_time(now)
    if not is_us_trading_day(now_et.date()):
        return False
    if last_sent_day == now_et.date().isoformat():
        return False
    try:
        at = parse_clock_time(time_et)
    except ValueError:
        return False
    return _within(now_et, at, MORNING_NOTE_CATCHUP)


def last_trading_day_of_week(day: date) -> date | None:
    """The last US trading day of the Monday-to-Friday week containing `day`
    (None for a week with no trading day at all)."""
    friday = day - timedelta(days=day.weekday()) + timedelta(days=_FRIDAY)
    for offset in range(_FRIDAY + 1):
        candidate = friday - timedelta(days=offset)
        if is_us_trading_day(candidate):
            return candidate
    return None


def weekly_digest_due(now: datetime, *, enabled: bool, last_sent_day: str | None) -> bool:
    """True when the weekly digest should be sent at `now`: switched on, today is the
    last trading day of the week (Friday, or Thursday when Friday is a holiday), inside
    the window that starts at 16:30 ET, and not already sent today."""
    if not enabled:
        return False
    now_et = to_market_time(now)
    day = now_et.date()
    if last_trading_day_of_week(day) != day:
        return False
    if last_sent_day == day.isoformat():
        return False
    return _within(now_et, WEEKLY_DIGEST_TIME_ET, WEEKLY_DIGEST_CATCHUP)


def week_start_utc(now: datetime) -> datetime:
    """Monday 00:00 New York time of the week containing `now`, as naive UTC."""
    now_et = to_market_time(now)
    monday = now_et.date() - timedelta(days=now_et.weekday())
    start_et = datetime.combine(monday, time(0, 0), tzinfo=now_et.tzinfo)
    return start_et.astimezone(timezone.utc).replace(tzinfo=None)
