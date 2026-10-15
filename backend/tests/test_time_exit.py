"""Shared bar fixtures for the exit tests (hold mode uses them), plus the bar-finality rules and the exit-reason mix.

The swing time limit that used to be tested here is gone. Every case uses fake bars with real dates and an
injected clock; nothing here reads the real time or the network.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.data_providers.base import QuoteData
from app.markets import is_daily_bar_final, is_us_trading_day
from app.portfolio.models import PaperPosition
from app.portfolio.stats import compute_portfolio_stats

NY = ZoneInfo("America/New_York")
STARTING_CASH = 100_000.0

# Friday 4 Sep 2026 is the entry day in most tests: the next calendar days are
# a weekend and Labor Day (Mon 7 Sep), so "bars, not calendar days" is exercised.
ENTRY_DAY = date(2026, 9, 4)
ENTRY_OPENED_AT = datetime(2026, 9, 4, 15, 0)  # naive UTC = 11:00 ET, inside the session


class BarsProvider:
    name = "fake"

    def __init__(self, bars: list[dict]):
        self._bars = bars

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        return pd.DataFrame(self._bars)

    def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(symbol=symbol, price=100.0, change_pct_24h=0.0, volume=1_000.0, avg_volume_20d=1_000_000.0)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def trading_days_after(start: date, n: int) -> list[date]:
    """The next n real US trading days after `start` (weekends and NYSE holidays skipped)."""
    days, day = [], start
    while len(days) < n:
        day += timedelta(days=1)
        if is_us_trading_day(day):
            days.append(day)
    return days


def bar(day: date, *, o=100.0, h=101.0, low=99.0, c=100.0, tz=None) -> dict:
    stamp = pd.Timestamp(day)
    if tz is not None:
        stamp = stamp.tz_localize(tz)
    return {"date": stamp, "open": o, "high": h, "low": low, "close": c, "volume": 1_000_000.0}


def quiet_bars(n: int, *, close_of_last: float = 101.0, tz=None) -> list[dict]:
    """The entry bar followed by n quiet trading days that never reach the 95 stop or the 110 target."""
    rows = [bar(ENTRY_DAY, tz=tz)]
    days = trading_days_after(ENTRY_DAY, n)
    for i, day in enumerate(days):
        rows.append(bar(day, c=close_of_last if i == n - 1 else 100.0, tz=tz))
    return rows


def make_position(session: Session, *, direction="long", symbol="AAPL", opened_at=ENTRY_OPENED_AT) -> PaperPosition:
    short = direction == "short"
    position = PaperPosition(
        symbol=symbol,
        direction=direction,
        entry_price=100.0,
        stop_loss=105.0 if short else 95.0,
        tp1=90.0 if short else 110.0,
        tp2=80.0 if short else 120.0,
        shares=10,
        opened_at=opened_at,
    )
    session.add(position)
    session.commit()
    session.refresh(position)
    return position


def clock_at_et(day: date, hour: int, minute: int = 0):
    moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY).astimezone(timezone.utc).replace(tzinfo=None)
    return lambda: moment


# ------------------------------------------------------------------- crypto day


def test_crypto_bar_is_final_only_after_its_utc_day_ends():
    # BTC-USD trades around the clock; its daily bar for 4 Sep runs to 00:00 UTC on 5 Sep.
    assert not is_daily_bar_final("BTC-USD", date(2026, 9, 4), datetime(2026, 9, 4, 23, 59))
    assert is_daily_bar_final("BTC-USD", date(2026, 9, 4), datetime(2026, 9, 5, 0, 0))


# ------------------------------------------------------------ the calendar rule


def test_equity_bar_finality_follows_the_session_close():
    friday = date(2026, 9, 4)
    assert not is_daily_bar_final("AAPL", friday, clock_at_et(friday, 12)())
    assert not is_daily_bar_final("AAPL", friday, clock_at_et(friday, 16, 0)())  # closing bell, still settling
    assert is_daily_bar_final("AAPL", friday, clock_at_et(friday, 16, 30)())
    assert is_daily_bar_final("AAPL", friday, clock_at_et(date(2026, 9, 5), 9)())  # the next morning


def test_early_close_day_is_final_after_one_pm_not_four():
    day_after_thanksgiving = date(2026, 11, 27)  # NYSE closes at 1:00 pm
    assert not is_daily_bar_final("AAPL", day_after_thanksgiving, clock_at_et(day_after_thanksgiving, 13, 20)())
    assert is_daily_bar_final("AAPL", day_after_thanksgiving, clock_at_et(day_after_thanksgiving, 13, 31)())


def test_a_bar_dated_on_a_non_session_day_counts_as_final():
    assert is_daily_bar_final("AAPL", date(2026, 9, 5), clock_at_et(date(2026, 9, 5), 12)())  # a Saturday


# --------------------------------------------------------- settings and the mix


def test_the_exit_reason_mix_is_counted_from_closed_rows(session: Session):
    provider = BarsProvider([])
    for i, reason in enumerate(("stop_hit", "time_exit", "time_exit", "tp1_hit", None)):
        row = make_position(session, symbol=f"SYM{i}")
        row.status = "closed"
        row.close_reason = reason
        row.realized_pnl = 1.0
        row.realized_r = 0.1
        session.add(row)
    make_position(session, symbol="STILLOPEN")  # open rows are not part of the mix
    session.commit()

    stats = compute_portfolio_stats(session, provider, STARTING_CASH)

    assert stats.exit_reasons == {"stop_hit": 1, "time_exit": 2, "tp1_hit": 1, "unknown": 1}
