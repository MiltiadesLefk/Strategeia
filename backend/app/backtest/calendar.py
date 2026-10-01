"""The two moments of a simulated trading day.

Live, the auto-scan decides shortly after the open from the previous close plus
the current price (the market-open pass runs at 09:45 ET). The backtest uses that
same convention: on every trading day t it decides at DECISION_DELAY after the
open, knowing the bars through t-1 and t's opening price, and fills near that
open. The exit scan then runs after the close, once the day's bar has settled
(markets.POST_CLOSE_GRACE after the bell: 16:30 ET on a normal day, 13:30 on an
early-close day), against the bars through t.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.markets import POST_CLOSE_GRACE, is_us_trading_day, us_session_bounds

# 15 minutes after the 09:30 open = 09:45 ET, the same moment the live market-open
# pass uses (services.deferred_evaluation_service.REDO_DELAY_AFTER_OPEN).
DECISION_DELAY = timedelta(minutes=15)


def _naive_utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def decision_moment(day: date) -> datetime:
    """Naive UTC moment the strategy decides on `day` (09:45 ET). `day` must be a trading day."""
    bounds = us_session_bounds(day)
    if bounds is None:
        raise ValueError(f"{day} is not a US trading day")
    return _naive_utc(bounds.open + DECISION_DELAY)


def close_moment(day: date) -> datetime:
    """Naive UTC moment the exit scan and the day's equity are taken (the day's
    bar is final): 16:30 ET, or 13:30 ET on an early-close day."""
    bounds = us_session_bounds(day)
    if bounds is None:
        raise ValueError(f"{day} is not a US trading day")
    return _naive_utc(bounds.close + POST_CLOSE_GRACE)


def trading_days(start: date, end: date) -> list[date]:
    """Every US trading day with start <= day <= end (holidays and weekends skipped)."""
    days: list[date] = []
    day = start
    while day <= end:
        if is_us_trading_day(day):
            days.append(day)
        day += timedelta(days=1)
    return days
