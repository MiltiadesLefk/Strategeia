"""Hourly bars in the exit scan: two things a daily bar cannot show.

1. A day whose range holds BOTH the stop and TP1. The old rule always took the
   stop (cautious on purpose). The hourly bars of that day usually show which
   level was touched first; when they cannot be used the stop is still taken, and
   the position says it was decided that way.
2. The rest of the ENTRY day. The daily walk skips the entry bar (it is spent: the
   entry is priced at that day's close/quote), but a stop touched later the same
   day, after the position existed, used to be missed, and a missed stop only
   ever deletes losses.

Everything runs on fake bars and an injected clock: no network, no real time.
Sept 2026 dates: Fri 4 Sep is the entry day, Mon 7 Sep is Labor Day, Tue 8 Sep is
the next session. All hourly bars are stamped the way yfinance does it for a US
stock (America/New_York, on the half hour: 09:30 ... 15:30).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.data_providers import cache as provider_cache
from app.data_providers.base import AllProvidersFailedError, QuoteData
from app.portfolio.engine import PaperTradingEngine
from app.portfolio.models import EquitySnapshot, PaperPosition

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
STARTING_CASH = 100_000.0

ENTRY_DAY = date(2026, 9, 4)
NEXT_DAY = date(2026, 9, 8)
# 11:00 ET on the entry day: inside the 10:30 hourly bar, after the 09:30 one.
ENTRY_OPENED_AT = datetime(2026, 9, 4, 15, 0)

QUIET = (100.0, 101.0, 99.0, 100.0)  # open, high, low, close: never reaches 95 or 110


def et(day: date, hour: int, minute: int = 0) -> datetime:
    """A New York wall-clock moment as naive UTC."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY).astimezone(UTC).replace(tzinfo=None)


def clock_at_et(day: date, hour: int, minute: int = 0):
    moment = et(day, hour, minute)
    return lambda: moment


class HourlyProvider:
    """Daily bars for interval "1d", hourly bars for "1h". `hourly_mode` decides what
    "1h" does: "frame" (the given hours), "fail" (every provider down), "daily" (a
    provider that ignores the interval and returns daily bars), "unsupported"."""

    name = "fake"

    def __init__(self, daily: list[dict], hourly: list[dict] | None = None, hourly_mode: str = "frame"):
        self._daily = daily
        self._hourly = hourly or []
        self.hourly_mode = hourly_mode
        self.calls: list[tuple[str, str, str]] = []  # (symbol, period, interval)
        self.hourly_fresh_flags: list[bool] = []

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        self.calls.append((symbol, period, interval))
        if interval != "1h":
            return pd.DataFrame(self._daily)
        self.hourly_fresh_flags.append(provider_cache._fresh_only.get())
        if self.hourly_mode == "fail":
            raise AllProvidersFailedError("hourly down")
        if self.hourly_mode == "unsupported":
            raise NotImplementedError("no hourly bars")
        if self.hourly_mode == "daily":
            return pd.DataFrame(self._daily)
        return pd.DataFrame(self._hourly)

    def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(symbol=symbol, price=100.0, change_pct_24h=0.0, volume=1_000.0, avg_volume_20d=1_000_000.0)

    @property
    def hourly_calls(self) -> list[tuple[str, str, str]]:
        return [call for call in self.calls if call[2] == "1h"]


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def daily_bar(day: date, o=100.0, h=101.0, low=99.0, c=100.0, tz=None) -> dict:
    stamp = pd.Timestamp(day)
    if tz is not None:
        stamp = stamp.tz_localize(tz)
    return {"date": stamp, "open": o, "high": h, "low": low, "close": c, "volume": 1e6}


def us_day_hours(day: date, overrides: dict[int, tuple[float, float, float, float]] | None = None) -> list[dict]:
    """The seven hourly bars of a regular US session (09:30 ... 15:30 ET), quiet
    except where `overrides` gives (open, high, low, close) for an hour index."""
    overrides = overrides or {}
    rows = []
    for i in range(7):
        o, h, low, c = overrides.get(i, QUIET)
        start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY) + timedelta(hours=i)
        rows.append({"date": pd.Timestamp(start), "open": o, "high": h, "low": low, "close": c, "volume": 1e5})
    return rows


def make_position(
    session: Session,
    *,
    direction="long",
    symbol="AAPL",
    opened_at=ENTRY_OPENED_AT,
    entry_day_check: str | None = "hourly",
) -> PaperPosition:
    """A position at 100 (long: stop 95, TP1 110; short: stop 105, TP1 90). Most
    tests mark its entry day as already checked so they are about the later days."""
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
        entry_day_check=entry_day_check,
    )
    session.add(position)
    session.commit()
    session.refresh(position)
    return position


def build(session: Session, provider: HourlyProvider, *, now, **kwargs) -> PaperTradingEngine:
    return PaperTradingEngine(session, provider, STARTING_CASH, clock=now, **kwargs)


def ambiguous_long_day(*, tz=None, **bar_overrides) -> list[dict]:
    """The entry bar, then 8 Sep: opens 100, reaches 111 and 94, so both the 95
    stop and the 110 target sit inside the day."""
    day = dict(o=100.0, h=111.0, low=94.0, c=100.0)
    day.update(bar_overrides)
    return [daily_bar(ENTRY_DAY, tz=tz), daily_bar(NEXT_DAY, tz=tz, **day)]


# ===================================================== 1. both levels in one day


@pytest.mark.parametrize("tz", [None, "America/New_York"], ids=["naive-daily", "tz-aware-daily"])
def test_target_first_in_the_hours_closes_at_the_target(session: Session, tz):
    hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {2: (100, 110.5, 99, 110), 4: (110, 110, 94, 96)})
    provider = HourlyProvider(ambiguous_long_day(tz=tz), hours)
    position = make_position(session)

    closed = build(session, provider, now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)

    assert [p.id for p in closed] == [position.id]
    assert position.close_reason == "tp1_hit"  # the old rule took the stop here
    assert position.close_price == pytest.approx(110.0)
    assert position.exit_resolution == "hourly"


@pytest.mark.parametrize("tz", [None, "America/New_York"], ids=["naive-daily", "tz-aware-daily"])
def test_stop_first_in_the_hours_closes_at_the_stop(session: Session, tz):
    hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {2: (100, 101, 94, 96), 4: (96, 110.5, 96, 110)})
    provider = HourlyProvider(ambiguous_long_day(tz=tz), hours)
    position = make_position(session)

    build(session, provider, now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)

    assert position.close_reason == "stop_hit"
    assert position.close_price == pytest.approx(95.0)
    assert position.exit_resolution == "hourly"


def test_short_mirrors_both_orders(session: Session):
    daily = [daily_bar(ENTRY_DAY), daily_bar(NEXT_DAY, o=100, h=106, low=89, c=100)]  # stop 105 and TP1 90 inside
    # target first: low reaches 89 in hour 2, the 106 high only in hour 4
    first = HourlyProvider(
        daily, us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {2: (100, 101, 89, 90), 4: (90, 106, 90, 100)})
    )
    short_tp = make_position(session, direction="short")
    build(session, first, now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)
    assert (short_tp.close_reason, short_tp.close_price, short_tp.exit_resolution) == ("tp1_hit", 90.0, "hourly")

    # stop first: the 106 high in hour 2, the 89 low only in hour 4
    second = HourlyProvider(
        daily, us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {2: (100, 106, 99, 104), 4: (104, 104, 89, 90)})
    )
    short_stop = make_position(session, direction="short", symbol="MSFT")
    build(session, second, now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)
    assert (short_stop.close_reason, short_stop.close_price, short_stop.exit_resolution) == ("stop_hit", 105.0, "hourly")


def test_unavailable_hourly_bars_fall_back_to_stop_first_and_say_so(session: Session):
    for mode in ("fail", "unsupported", "daily"):
        session.rollback()
        provider = HourlyProvider(ambiguous_long_day(), hourly_mode=mode)
        position = make_position(session, symbol=f"T{mode[:3].upper()}")

        build(session, provider, now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)

        assert position.close_reason == "stop_hit", mode
        assert position.close_price == pytest.approx(95.0), mode
        assert position.exit_resolution == "daily_ambiguous_stop_first", mode


def test_hours_that_do_not_reach_both_levels_are_not_trusted(session: Session):
    # The daily bar saw 94, but the hours on file bottom out at 96: an hour is
    # missing, and the missing one could be where the stop (or target) came first.
    hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {2: (100, 110.5, 96, 110)})
    provider = HourlyProvider(ambiguous_long_day(), hours)
    position = make_position(session)

    build(session, provider, now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)

    assert (position.close_reason, position.exit_resolution) == ("stop_hit", "daily_ambiguous_stop_first")


def test_both_levels_in_one_hour_take_the_stop(session: Session):
    hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {2: (100, 111, 94, 100)})
    position = make_position(session)

    build(session, HourlyProvider(ambiguous_long_day(), hours), now=clock_at_et(NEXT_DAY, 17)).mark_to_market(
        snapshot=False
    )

    assert position.close_reason == "stop_hit"
    assert position.close_price == pytest.approx(95.0)
    assert position.exit_resolution == "hourly_ambiguous_stop_first"


def test_gap_through_the_stop_inside_the_hours_fills_at_that_hours_open(session: Session):
    # Hour 3 opens at 93, already below the 95 stop: a market order fills there, not at 95.
    hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {3: (93, 94, 92, 93), 5: (95, 111, 95, 110)})
    position = make_position(session)

    build(
        session, HourlyProvider(ambiguous_long_day(low=92), hours), now=clock_at_et(NEXT_DAY, 17)
    ).mark_to_market(snapshot=False)

    assert position.close_reason == "stop_hit"
    assert position.close_price == pytest.approx(93.0)


def test_gap_through_the_target_inside_the_hours_fills_at_that_hours_open(session: Session):
    # Hour 3 opens at 112, above the 110 limit: a resting limit fills at the better price.
    hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {3: (112, 113, 111, 112), 5: (100, 101, 94, 95)})
    position = make_position(session)

    build(
        session, HourlyProvider(ambiguous_long_day(h=113), hours), now=clock_at_et(NEXT_DAY, 17)
    ).mark_to_market(snapshot=False)

    assert position.close_reason == "tp1_hit"
    assert position.close_price == pytest.approx(112.0)


def test_stop_slippage_applies_to_an_hourly_stop_and_not_to_an_hourly_target(session: Session):
    stop_hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {2: (100, 101, 94, 96), 4: (96, 111, 96, 110)})
    stopped = make_position(session)
    build(session, HourlyProvider(ambiguous_long_day(), stop_hours), now=clock_at_et(NEXT_DAY, 17), slippage_bps=10.0).mark_to_market(
        snapshot=False
    )
    assert stopped.close_price == pytest.approx(95.0 * (1 - 0.001))

    tp_hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {2: (100, 111, 99, 110), 4: (110, 110, 94, 96)})
    target = make_position(session, symbol="MSFT")
    build(session, HourlyProvider(ambiguous_long_day(), tp_hours), now=clock_at_et(NEXT_DAY, 17), slippage_bps=10.0).mark_to_market(
        snapshot=False
    )
    assert target.close_price == pytest.approx(110.0)


def test_a_bar_that_opens_below_the_stop_needs_no_hourly_look(session: Session):
    # The open itself triggers the stop. Even hours that "say" the target came first must not matter.
    daily = [daily_bar(ENTRY_DAY), daily_bar(NEXT_DAY, o=93, h=111, low=90, c=100)]
    hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {0: (93, 111, 93, 110), 4: (110, 110, 90, 92)})
    provider = HourlyProvider(daily, hours)
    position = make_position(session)

    build(session, provider, now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)

    assert position.close_reason == "stop_hit"
    assert position.close_price == pytest.approx(93.0)
    assert position.exit_resolution == "daily"
    assert provider.hourly_calls == []


# ============================================ 2. the rest of the entry day


def test_stop_touched_after_entry_on_the_entry_day_closes_the_position(session: Session):
    # Entry 11:00 ET. The 13:30 bar trades down to 94: after the position existed.
    hours = us_day_hours(ENTRY_DAY, {4: (100, 100.5, 94, 95)})
    provider = HourlyProvider([daily_bar(ENTRY_DAY)], hours)
    position = make_position(session, entry_day_check=None)

    closed = build(session, provider, now=clock_at_et(ENTRY_DAY, 14, 30)).mark_to_market(snapshot=False)

    assert [p.id for p in closed] == [position.id]
    assert position.close_reason == "stop_hit"
    assert position.close_price == pytest.approx(95.0)
    assert position.exit_resolution == "hourly"
    assert position.entry_day_check == "hourly"


def test_target_touched_after_entry_on_the_entry_day_closes_the_position(session: Session):
    hours = us_day_hours(ENTRY_DAY, {3: (100, 111, 100, 110)})
    position = make_position(session, entry_day_check=None)

    build(session, HourlyProvider([daily_bar(ENTRY_DAY)], hours), now=clock_at_et(ENTRY_DAY, 14, 30)).mark_to_market(
        snapshot=False
    )

    assert (position.close_reason, position.close_price) == ("tp1_hit", 110.0)


def test_a_touch_before_the_entry_does_not_close_it(session: Session):
    # The 09:30 bar (ended 10:30, before the 11:00 entry) went down to 94. Not our trade yet.
    hours = us_day_hours(ENTRY_DAY, {0: (100, 101, 94, 99)})
    position = make_position(session, entry_day_check=None)

    closed = build(session, HourlyProvider([daily_bar(ENTRY_DAY)], hours), now=clock_at_et(ENTRY_DAY, 17)).mark_to_market(
        snapshot=False
    )

    assert closed == []
    assert position.status == "open"
    assert position.entry_day_check == "hourly"  # the whole day was covered, nothing touched


def test_the_hour_containing_the_entry_counts_for_the_stop_only(session: Session):
    # The 10:30 bar is under way at the 11:00 entry. Its low breaks the stop (open below
    # it too): that may have been before the entry, but the cautious reading is to take
    # the stop, filled at the stop price, not at the bar's open from before the entry.
    stop_hours = us_day_hours(ENTRY_DAY, {1: (90, 101, 90, 99)})
    stopped = make_position(session, entry_day_check=None)
    build(session, HourlyProvider([daily_bar(ENTRY_DAY)], stop_hours), now=clock_at_et(ENTRY_DAY, 12)).mark_to_market(
        snapshot=False
    )
    assert (stopped.close_reason, stopped.close_price) == ("stop_hit", 95.0)

    # A target in that same hour is NOT taken: crediting a win the position may never have had.
    target_hours = us_day_hours(ENTRY_DAY, {1: (100, 111, 100, 105)})
    held = make_position(session, symbol="MSFT", entry_day_check=None)
    build(session, HourlyProvider([daily_bar(ENTRY_DAY)], target_hours), now=clock_at_et(ENTRY_DAY, 12)).mark_to_market(
        snapshot=False
    )
    assert held.status == "open"


def test_a_position_opened_exactly_on_a_bar_start_checks_that_whole_bar(session: Session):
    # Opened at 10:30 ET sharp: the 10:30 bar is wholly after the entry, so even a target in it counts.
    hours = us_day_hours(ENTRY_DAY, {1: (100, 111, 100, 105)})
    position = make_position(session, opened_at=et(ENTRY_DAY, 10, 30), entry_day_check=None)

    build(session, HourlyProvider([daily_bar(ENTRY_DAY)], hours), now=clock_at_et(ENTRY_DAY, 12)).mark_to_market(
        snapshot=False
    )

    assert (position.close_reason, position.close_price) == ("tp1_hit", 110.0)


def test_short_on_the_entry_day_mirrors(session: Session):
    hours = us_day_hours(ENTRY_DAY, {4: (100, 106, 100, 105)})
    position = make_position(session, direction="short", entry_day_check=None)

    build(session, HourlyProvider([daily_bar(ENTRY_DAY)], hours), now=clock_at_et(ENTRY_DAY, 14, 30)).mark_to_market(
        snapshot=False
    )

    assert (position.close_reason, position.close_price) == ("stop_hit", 105.0)


def test_entry_day_is_still_open_so_it_is_not_marked_complete_yet(session: Session):
    hours = [row for row in us_day_hours(ENTRY_DAY) if row["date"].hour <= 13]  # bars up to 13:30
    position = make_position(session, entry_day_check=None)

    closed = build(session, HourlyProvider([daily_bar(ENTRY_DAY)], hours), now=clock_at_et(ENTRY_DAY, 14, 15)).mark_to_market(
        snapshot=False
    )

    assert closed == []
    assert position.entry_day_check is None  # looked at, but the 14:30 and 15:30 bars are still to come


def test_early_close_day_is_complete_after_the_one_oclock_close(session: Session):
    # Fri 28 Nov 2025 closes at 1:00 pm ET: the last hourly bar starts at 12:30.
    day = date(2025, 11, 28)
    hours = [row for row in us_day_hours(day) if row["date"].hour <= 12]  # 09:30 ... 12:30
    position = make_position(session, opened_at=et(day, 11, 0), entry_day_check=None)

    build(session, HourlyProvider([daily_bar(day)], hours), now=clock_at_et(day, 14)).mark_to_market(snapshot=False)

    assert position.status == "open"
    assert position.entry_day_check == "hourly"


def test_hourly_bars_that_never_arrive_leave_the_entry_day_marked_daily_only(session: Session):
    provider = HourlyProvider([daily_bar(ENTRY_DAY)], hourly_mode="fail")
    position = make_position(session, entry_day_check=None)

    # The evening of the entry day: the bars may still turn up, so ask again next sweep.
    build(session, provider, now=clock_at_et(ENTRY_DAY, 17)).mark_to_market(snapshot=False)
    assert position.entry_day_check is None and position.status == "open"

    # Two days later they are not coming: the old daily rule stands, and the row says so.
    build(session, provider, now=clock_at_et(NEXT_DAY, 12)).mark_to_market(snapshot=False)
    assert position.entry_day_check == "daily_only"
    assert position.status == "open"


def test_an_entry_day_older_than_the_hourly_history_is_daily_only(session: Session):
    position = make_position(session, opened_at=datetime(2025, 1, 6, 15, 0), entry_day_check=None)
    provider = HourlyProvider([daily_bar(date(2025, 1, 6))], hourly_mode="fail")

    build(session, provider, now=clock_at_et(NEXT_DAY, 12)).mark_to_market(snapshot=False)

    assert position.entry_day_check == "daily_only"
    assert provider.hourly_calls == []  # nothing to ask for: no history reaches that far back


def test_entry_day_exit_is_found_even_when_the_position_is_older_and_later_bars_exist(session: Session):
    # A position from before this check existed (entry day never checked): the hourly look
    # still finds the entry-day stop, ahead of anything the daily walk would say.
    daily = [daily_bar(ENTRY_DAY), daily_bar(NEXT_DAY, o=100, h=103, low=99, c=102)]
    hours = us_day_hours(ENTRY_DAY, {5: (99, 99, 94, 95)}) + us_day_hours(NEXT_DAY)
    position = make_position(session, entry_day_check=None)

    build(session, HourlyProvider(daily, hours), now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)

    assert position.close_reason == "stop_hit"
    assert position.entry_day_check == "hourly"


def test_crypto_entry_day_uses_utc_days(session: Session):
    day = date(2026, 9, 4)
    opened = datetime(2026, 9, 4, 10, 0)  # naive UTC
    hours = []
    for hour in range(24):
        low = 94.0 if hour == 14 else 99.0
        start = pd.Timestamp(datetime(2026, 9, 4, hour, tzinfo=UTC))
        hours.append({"date": start, "open": 100.0, "high": 101.0, "low": low, "close": 100.0, "volume": 1})
    provider = HourlyProvider([daily_bar(day)], hours)
    position = make_position(session, symbol="BTC-USD", opened_at=opened, entry_day_check=None)

    closed = build(session, provider, now=lambda: datetime(2026, 9, 4, 20, 0)).mark_to_market(snapshot=False)

    assert [p.id for p in closed] == [position.id]
    assert (position.close_reason, position.close_price) == ("stop_hit", 95.0)


def test_crypto_entry_day_is_complete_only_after_utc_midnight(session: Session):
    hours = [
        {"date": pd.Timestamp(datetime(2026, 9, 4, h, tzinfo=UTC)), "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.0, "volume": 1}
        for h in range(24)
    ]
    provider = HourlyProvider([daily_bar(date(2026, 9, 4))], hours)
    position = make_position(session, symbol="BTC-USD", opened_at=datetime(2026, 9, 4, 10, 0), entry_day_check=None)

    build(session, provider, now=lambda: datetime(2026, 9, 4, 23, 30)).mark_to_market(snapshot=False)
    assert position.entry_day_check is None  # an hour of the UTC day is still to come

    build(session, provider, now=lambda: datetime(2026, 9, 5, 0, 30)).mark_to_market(snapshot=False)
    assert position.entry_day_check == "hourly"


# ========================================================== cost and invariants


def test_no_hourly_request_when_nothing_is_ambiguous_and_the_entry_day_is_checked(session: Session):
    daily = [daily_bar(ENTRY_DAY), daily_bar(NEXT_DAY, o=100, h=103, low=99, c=102)]
    provider = HourlyProvider(daily, us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY))
    make_position(session)  # entry day already checked
    make_position(session, symbol="MSFT")

    closed = build(session, provider, now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)

    assert closed == []
    assert provider.hourly_calls == []


def test_a_clean_stop_does_not_ask_for_hourly_bars_either(session: Session):
    daily = [daily_bar(ENTRY_DAY), daily_bar(NEXT_DAY, o=100, h=103, low=94, c=96)]  # stop only
    provider = HourlyProvider(daily, us_day_hours(NEXT_DAY))
    position = make_position(session)

    build(session, provider, now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)

    assert position.close_reason == "stop_hit"
    assert position.exit_resolution == "daily"
    assert provider.hourly_calls == []


def test_one_hourly_request_per_symbol_per_sweep(session: Session):
    hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {2: (100, 110.5, 99, 110), 4: (110, 110, 94, 96)})
    provider = HourlyProvider(ambiguous_long_day(), hours)
    first = make_position(session, entry_day_check=None)
    second = make_position(session, entry_day_check=None)  # same symbol, same ambiguous day

    build(session, provider, now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)

    assert len(provider.hourly_calls) == 1
    assert first.close_reason == second.close_reason == "tp1_hit"


def test_hourly_requests_run_inside_fresh_data_only(session: Session):
    provider = HourlyProvider(ambiguous_long_day(), hourly_mode="fail")
    make_position(session)

    build(session, provider, now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)

    assert provider.hourly_fresh_flags == [True]


def test_intraday_exits_can_be_switched_off(session: Session):
    hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {2: (100, 110.5, 99, 110), 4: (110, 110, 94, 96)})
    provider = HourlyProvider(ambiguous_long_day(), hours)
    position = make_position(session, entry_day_check=None)

    build(session, provider, now=clock_at_et(NEXT_DAY, 17), intraday_exits=False).mark_to_market(snapshot=False)

    assert provider.hourly_calls == []
    assert (position.close_reason, position.exit_resolution) == ("stop_hit", "daily_ambiguous_stop_first")
    assert position.entry_day_check is None


def test_a_read_only_sweep_that_closes_on_hourly_bars_writes_no_equity_snapshot(session: Session):
    hours = us_day_hours(ENTRY_DAY, {4: (100, 100.5, 94, 95)})
    make_position(session, entry_day_check=None)

    build(session, HourlyProvider([daily_bar(ENTRY_DAY)], hours), now=clock_at_et(ENTRY_DAY, 14, 30)).mark_to_market(
        snapshot=False
    )

    assert session.exec(select(EquitySnapshot)).all() == []


def test_a_plain_daily_exit_is_recorded_as_daily_and_a_manual_close_has_no_resolution(session: Session):
    daily = [daily_bar(ENTRY_DAY), daily_bar(NEXT_DAY, o=100, h=103, low=94, c=96)]
    scanned = make_position(session)
    build(session, HourlyProvider(daily), now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)
    assert scanned.exit_resolution == "daily"

    manual = make_position(session, symbol="MSFT")
    build(session, HourlyProvider(daily), now=clock_at_et(NEXT_DAY, 17)).close_position(manual, 101.0, "manual", snapshot=False)
    assert manual.exit_resolution is None


# ============================================================ excursion (MFE/MAE)


def test_excursion_of_an_hourly_resolved_target_ignores_the_stop_side_that_came_after(session: Session):
    # Target first (hour 2); the day's 94 low is hour 4, AFTER the position left.
    hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {1: (100, 102, 98, 101), 2: (101, 110.5, 100, 110), 4: (110, 110, 94, 96)})
    position = make_position(session)

    build(session, HourlyProvider(ambiguous_long_day(), hours), now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)

    assert position.close_reason == "tp1_hit"
    assert position.mfe_pct == pytest.approx(10.0)  # the 110 fill
    assert position.mae_pct == pytest.approx(2.0)  # hour 1's 98: not the day's 94, which came after the exit


def test_excursion_of_an_hourly_resolved_stop_counts_the_fill_not_the_later_target(session: Session):
    hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {1: (100, 103, 99, 101), 2: (101, 101, 94, 96), 4: (96, 111, 96, 110)})
    position = make_position(session)

    build(session, HourlyProvider(ambiguous_long_day(), hours), now=clock_at_et(NEXT_DAY, 17)).mark_to_market(snapshot=False)

    assert position.close_reason == "stop_hit"
    assert position.mae_pct == pytest.approx(5.0)  # the 95 fill
    assert position.mfe_pct == pytest.approx(3.0)  # hour 1's 103: not the day's 111, which came after


def test_excursion_of_an_entry_day_stop_uses_only_the_hours_after_the_entry(session: Session):
    # 09:30 bar (before entry) ranged 94..120; the entry hour 10:30 is not counted; 11:30 reaches 104.
    hours = us_day_hours(ENTRY_DAY, {0: (100, 120, 94, 99), 2: (100, 104, 99, 101), 4: (101, 101, 94, 95)})
    position = make_position(session, entry_day_check=None)

    build(session, HourlyProvider([daily_bar(ENTRY_DAY)], hours), now=clock_at_et(ENTRY_DAY, 14, 30)).mark_to_market(
        snapshot=False
    )

    assert position.close_reason == "stop_hit"
    assert position.mfe_pct == pytest.approx(4.0)
    assert position.mae_pct == pytest.approx(5.0)


def test_the_time_exit_still_works_with_the_hourly_machinery_on(session: Session):
    days = [date(2026, 9, 8), date(2026, 9, 9)]
    daily = [daily_bar(ENTRY_DAY)] + [daily_bar(d) for d in days]
    provider = HourlyProvider(daily, us_day_hours(ENTRY_DAY) + us_day_hours(days[0]) + us_day_hours(days[1]))
    position = make_position(session)

    build(session, provider, now=clock_at_et(date(2026, 9, 10), 12), max_holding_days=2).mark_to_market(snapshot=False)

    assert position.close_reason == "time_exit"
    assert position.exit_resolution == "daily"
    assert provider.hourly_calls == []


# ================================================================ pure helpers


def test_prepare_hourly_reads_new_york_stamps_and_naive_utc_stamps_alike():
    from app.portfolio.intraday import prepare_hourly

    aware = pd.DataFrame(us_day_hours(ENTRY_DAY))
    naive = aware.copy()
    naive["date"] = [pd.Timestamp(ts).tz_convert("UTC").tz_localize(None) for ts in aware["date"]]  # the same moments

    from_aware, from_naive = prepare_hourly(aware, "AAPL"), prepare_hourly(naive, "AAPL")

    assert from_aware is not None and from_naive is not None
    assert list(from_aware["start"]) == list(from_naive["start"])
    assert from_aware["start"].iloc[0] == pd.Timestamp(et(ENTRY_DAY, 9, 30))
    assert set(from_aware["day"]) == {ENTRY_DAY}


def test_prepare_hourly_refuses_a_frame_that_is_not_hourly():
    from app.portfolio.intraday import prepare_hourly

    daily = pd.DataFrame([daily_bar(ENTRY_DAY), daily_bar(NEXT_DAY)])
    assert prepare_hourly(daily, "AAPL") is None
    assert prepare_hourly(pd.DataFrame(), "AAPL") is None
    assert prepare_hourly(pd.DataFrame(us_day_hours(ENTRY_DAY)[:1]), "AAPL") is None  # one bar: nothing to walk


def test_crypto_days_are_utc_days_and_equity_days_are_new_york_days():
    from app.portfolio.intraday import market_day_of

    late_evening_et = datetime(2026, 9, 5, 2, 0)  # 22:00 ET on the 4th, 02:00 UTC on the 5th
    assert market_day_of("AAPL", late_evening_et) == date(2026, 9, 4)
    assert market_day_of("BTC-USD", late_evening_et) == date(2026, 9, 5)


def test_the_hourly_history_period_is_the_shortest_that_reaches_back():
    from app.portfolio.intraday import hourly_period_for

    today = date(2026, 10, 1)
    assert hourly_period_for(date(2026, 9, 29), today) == "5d"
    assert hourly_period_for(date(2026, 9, 1), today) == "3mo"  # 30 days: past the 1mo margin
    assert hourly_period_for(date(2026, 1, 1), today) == "1y"
    assert hourly_period_for(date(2024, 1, 1), today) is None
