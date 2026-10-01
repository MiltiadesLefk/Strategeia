"""The position time limit: a third way out after the stop and TP1.

A position that is still open after `max_holding_days` trading bars, and whose
stop and TP1 were never touched, is closed at that bar's close. These tests pin
the rules that matter: it counts bars (not calendar days), it comes after the
stop and TP1 on the same bar, it pays slippage, it never acts on a bar that has
not finished, and a limit of 0 leaves the old behaviour untouched.

Every case uses fake bars with real dates and an injected clock; nothing here
reads the real time or the network.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from app.data_providers.base import QuoteData
from app.markets import is_daily_bar_final, is_us_trading_day
from app.portfolio.engine import PaperTradingEngine
from app.portfolio.models import PaperPosition
from app.portfolio.stats import compute_portfolio_stats
from app.schemas.settings_schemas import SettingsUpdateRequest

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


def build(session, bars, *, limit, now, **kwargs) -> PaperTradingEngine:
    return PaperTradingEngine(
        session, BarsProvider(bars), STARTING_CASH, max_holding_days=limit, clock=now, **kwargs
    )


# ------------------------------------------------------------------ the rule


def test_closes_at_the_limit_bar_close_and_pays_slippage(session: Session):
    bars = quiet_bars(5, close_of_last=101.0)
    last_day = trading_days_after(ENTRY_DAY, 5)[-1]
    position = make_position(session)
    engine = build(session, bars, limit=5, now=clock_at_et(last_day + timedelta(days=1), 12), slippage_bps=10.0)

    closed = engine.mark_to_market(snapshot=False)

    assert [p.id for p in closed] == [position.id]
    assert position.close_reason == "time_exit"
    # A market order at the close: the long sells 10 bps below it, like a stop does.
    assert position.close_price == pytest.approx(101.0 * (1 - 0.001))
    assert position.status == "closed"


def test_short_time_exit_pays_slippage_the_other_way(session: Session):
    bars = quiet_bars(5, close_of_last=99.0)
    last_day = trading_days_after(ENTRY_DAY, 5)[-1]
    position = make_position(session, direction="short")
    engine = build(session, bars, limit=5, now=clock_at_et(last_day + timedelta(days=1), 12), slippage_bps=10.0)

    engine.mark_to_market(snapshot=False)

    assert position.close_reason == "time_exit"
    assert position.close_price == pytest.approx(99.0 * (1 + 0.001))


def test_realised_pnl_and_r_are_computed_like_any_other_close(session: Session):
    bars = quiet_bars(5, close_of_last=102.0)
    last_day = trading_days_after(ENTRY_DAY, 5)[-1]
    position = make_position(session)
    build(session, bars, limit=5, now=clock_at_et(last_day + timedelta(days=1), 12)).mark_to_market(snapshot=False)

    # (102 - 100) * 10 shares, no costs; risk is (100 - 95) * 10 = 50.
    assert position.realized_pnl == pytest.approx(20.0)
    assert position.realized_r == pytest.approx(0.4)


def test_not_closed_before_the_limit_bar(session: Session):
    bars = quiet_bars(4)
    last_day = trading_days_after(ENTRY_DAY, 4)[-1]
    position = make_position(session)
    closed = build(session, bars, limit=5, now=clock_at_et(last_day + timedelta(days=1), 12)).mark_to_market(snapshot=False)

    assert closed == []
    assert position.status == "open"


def test_bars_after_the_limit_bar_do_not_change_the_exit(session: Session):
    """The app can be off for days. The walk still finds the limit bar and closes
    there, at ITS close, rather than at some later bar's price."""
    bars = quiet_bars(7, close_of_last=60.0)  # a crash after the limit bar must not matter
    days = trading_days_after(ENTRY_DAY, 7)
    bars[6] = bar(days[5], c=100.0)  # day 6, after the limit bar
    bars[7] = bar(days[6], o=60.0, h=61.0, low=58.0, c=60.0)  # day 7 would hit the stop
    position = make_position(session)
    build(session, bars, limit=5, now=clock_at_et(days[-1] + timedelta(days=1), 12)).mark_to_market(snapshot=False)

    assert position.close_reason == "time_exit"
    assert position.close_price == pytest.approx(100.0)


# ---------------------------------------------------- order within the limit bar


def test_stop_on_the_limit_bar_beats_the_time_exit(session: Session):
    bars = quiet_bars(5)
    days = trading_days_after(ENTRY_DAY, 5)
    bars[-1] = bar(days[-1], o=100.0, h=101.0, low=94.0, c=99.0)  # dips through the 95 stop that day
    position = make_position(session)
    build(session, bars, limit=5, now=clock_at_et(days[-1] + timedelta(days=1), 12)).mark_to_market(snapshot=False)

    assert position.close_reason == "stop_hit"
    assert position.close_price == pytest.approx(95.0)


def test_tp1_on_the_limit_bar_beats_the_time_exit(session: Session):
    bars = quiet_bars(5)
    days = trading_days_after(ENTRY_DAY, 5)
    bars[-1] = bar(days[-1], o=100.0, h=111.0, low=99.0, c=104.0)
    position = make_position(session)
    build(session, bars, limit=5, now=clock_at_et(days[-1] + timedelta(days=1), 12)).mark_to_market(snapshot=False)

    assert position.close_reason == "tp1_hit"
    assert position.close_price == pytest.approx(110.0)


def test_stop_before_the_limit_still_closes_as_a_stop(session: Session):
    bars = quiet_bars(5)
    days = trading_days_after(ENTRY_DAY, 5)
    bars[2] = bar(days[1], o=96.0, h=97.0, low=90.0, c=98.0)  # day 2
    position = make_position(session)
    build(session, bars, limit=5, now=clock_at_et(days[-1] + timedelta(days=1), 12)).mark_to_market(snapshot=False)

    assert position.close_reason == "stop_hit"


# ----------------------------------------------------- an unfinished bar never exits


def test_a_still_forming_limit_bar_does_not_exit_while_the_market_is_open(session: Session):
    bars = quiet_bars(5)
    today = trading_days_after(ENTRY_DAY, 5)[-1]  # the limit bar is today's
    position = make_position(session)
    closed = build(session, bars, limit=5, now=clock_at_et(today, 11)).mark_to_market(snapshot=False)

    assert closed == []
    assert position.status == "open"


def test_a_limit_bar_inside_the_post_close_settling_window_waits(session: Session):
    bars = quiet_bars(5)
    today = trading_days_after(ENTRY_DAY, 5)[-1]
    position = make_position(session)
    build(session, bars, limit=5, now=clock_at_et(today, 16, 10)).mark_to_market(snapshot=False)

    assert position.status == "open"


def test_the_same_bar_exits_once_the_session_has_closed(session: Session):
    bars = quiet_bars(5, close_of_last=101.0)
    today = trading_days_after(ENTRY_DAY, 5)[-1]
    position = make_position(session)
    build(session, bars, limit=5, now=clock_at_et(today, 16, 31)).mark_to_market(snapshot=False)

    assert position.close_reason == "time_exit"
    assert position.close_price == pytest.approx(101.0)


def test_a_forming_bar_still_lets_the_stop_fire(session: Session):
    """The finality rule only gates the time exit: a stop or target touched on
    today's partial bar is real the moment it is touched."""
    bars = quiet_bars(5)
    today = trading_days_after(ENTRY_DAY, 5)[-1]
    bars[-1] = bar(today, o=100.0, h=100.5, low=94.0, c=96.0)
    position = make_position(session)
    build(session, bars, limit=5, now=clock_at_et(today, 11)).mark_to_market(snapshot=False)

    assert position.close_reason == "stop_hit"


def test_a_timezone_aware_new_york_bar_date_is_read_as_its_own_day(session: Session):
    """yfinance stamps equity daily bars at midnight New York time (tz-aware).
    Converted to UTC that is 04:00 or 05:00 on the same date, and the bar must
    still be today's, not yesterday's."""
    bars = quiet_bars(5, tz="America/New_York")
    today = trading_days_after(ENTRY_DAY, 5)[-1]
    position = make_position(session)

    build(session, bars, limit=5, now=clock_at_et(today, 11)).mark_to_market(snapshot=False)
    assert position.status == "open"  # still forming

    build(session, bars, limit=5, now=clock_at_et(today, 16, 45)).mark_to_market(snapshot=False)
    assert position.close_reason == "time_exit"


# ------------------------------------------------------- counting bars, not days


def test_weekends_and_holidays_do_not_count(session: Session):
    """Entry Friday 4 Sep. By Friday 11 Sep a calendar week has passed, but with
    Labor Day off only four trading days have, so a 5-day limit has not fired."""
    bars = quiet_bars(4)
    assert [d.isoformat() for d in trading_days_after(ENTRY_DAY, 4)] == ["2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]
    position = make_position(session)
    build(session, bars, limit=5, now=clock_at_et(date(2026, 9, 12), 12)).mark_to_market(snapshot=False)
    assert position.status == "open"

    # The fifth trading day (Monday 14 Sep) is when it closes.
    bars = quiet_bars(5)
    build(session, bars, limit=5, now=clock_at_et(date(2026, 9, 15), 12)).mark_to_market(snapshot=False)
    assert position.close_reason == "time_exit"
    assert position.closed_at is not None


def test_the_entry_bar_is_day_zero_not_day_one(session: Session):
    """Limit 1 closes at the close of the FIRST bar after entry, never at the
    entry bar itself (which is spent: entry is its close)."""
    bars = quiet_bars(1, close_of_last=103.0)
    position = make_position(session)
    build(session, bars, limit=1, now=clock_at_et(date(2026, 9, 9), 12)).mark_to_market(snapshot=False)

    assert position.close_price == pytest.approx(103.0)


def test_the_age_is_unknown_when_the_entry_bar_is_outside_the_window(session: Session):
    """If the history starts after the position opened, the day count cannot be
    taken from real bars, so the time limit does not guess."""
    days = trading_days_after(ENTRY_DAY, 8)
    bars = [bar(d) for d in days]  # no bar on or before opened_at
    position = make_position(session)
    closed = build(session, bars, limit=3, now=clock_at_et(days[-1] + timedelta(days=1), 12)).mark_to_market(snapshot=False)

    assert closed == []
    assert position.status == "open"


# ------------------------------------------------------------------ off switch


@pytest.mark.parametrize("limit", [0, None])
def test_no_limit_means_a_stalled_position_stays_open(session: Session, limit):
    bars = quiet_bars(40)
    position = make_position(session)
    closed = build(session, bars, limit=limit, now=clock_at_et(date(2026, 12, 1), 12)).mark_to_market(snapshot=False)

    assert closed == []
    assert position.status == "open"


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


def test_max_holding_days_is_validated():
    assert SettingsUpdateRequest(max_holding_days=0).max_holding_days == 0
    assert SettingsUpdateRequest(max_holding_days=60).max_holding_days == 60
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(max_holding_days=-1)
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(max_holding_days=61)


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
