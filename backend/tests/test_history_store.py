"""The local price-history store, driven by a fake provider (no network).

The fake behaves like yfinance in the ways that matter here: it answers by
period string ("1mo", "10y", "max"), stamps bars with tz-aware New York
midnights, includes today's still-forming bar during a session, and can be told
to fail, lag, change source or re-base its adjusted prices.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.data_providers.base import AllProvidersFailedError
from app.data_providers.history_store import (
    BAR_COLUMNS,
    FillReport,
    HistoryStore,
    HistoryUnavailableError,
    normalize_bars,
    period_for_days,
)
from app.knowledge.point_in_time import as_of
from app.markets import is_us_trading_day

# Thursday 2026-10-01, 22:00 UTC = 18:00 New York: that day's bar is final.
EVENING = datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc)
# The same day at 11:00 New York: the session is running, today's bar is forming.
MID_SESSION = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)

PERIOD_DAYS = {"5d": 7, "1mo": 31, "3mo": 92, "6mo": 184, "1y": 366, "2y": 731, "5y": 1827, "10y": 3660}


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeProvider:
    """Daily bars for trading days only, from `listed_from` to the clock's day."""

    def __init__(self, clock: Clock, *, listed_from: date = date(2000, 1, 3), name: str = "yfinance"):
        self.clock = clock
        self.listed_from = listed_from
        self.name = name
        self.fail = False
        self.lag_days = 0  # drop this many of the newest bars (a provider running behind)
        self.missing: set[date] = set()  # days the provider simply has no bar for
        self.rebase_before: date | None = None  # a dividend: closes before this date shift...
        self.rebase_factor = 1.0  # ...by this factor
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _close(symbol: str, day: date) -> float:
        return 50.0 + (day.toordinal() % 97) + (hash(symbol) % 13)

    def _bars(self, symbol: str, period: str) -> pd.DataFrame:
        today = self.clock.now.astimezone(timezone.utc).date()
        horizon = None if period == "max" else today - timedelta(days=PERIOD_DAYS[period])
        days = []
        day = max(self.listed_from, horizon) if horizon else self.listed_from
        while day <= today:
            if is_us_trading_day(day) and day not in self.missing:
                days.append(day)
            day += timedelta(days=1)
        if self.lag_days:
            days = days[: -self.lag_days]
        closes = []
        for d in days:
            close = self._close(symbol, d)
            if self.rebase_before and d < self.rebase_before:
                close *= self.rebase_factor
            closes.append(close)
        close_arr = np.array(closes, dtype=float)
        return pd.DataFrame(
            {
                "date": pd.to_datetime(days).tz_localize("America/New_York"),
                "open": close_arr * 0.99,
                "high": close_arr * 1.01,
                "low": close_arr * 0.98,
                "close": close_arr,
                "volume": np.full(len(days), 1000.0),
            }
        )

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        self.calls.append((symbol, period))
        if self.fail:
            raise AllProvidersFailedError("provider down")
        return self._bars(symbol, period)


@pytest.fixture
def clock():
    return Clock(EVENING)


@pytest.fixture
def provider(clock):
    return FakeProvider(clock)


@pytest.fixture
def store(tmp_path, clock, provider):
    s = HistoryStore(tmp_path / "history.db", provider, clock=clock)
    yield s
    s.close()


def trading_days(start: date, end: date) -> list[date]:
    out, day = [], start
    while day <= end:
        if is_us_trading_day(day):
            out.append(day)
        day += timedelta(days=1)
    return out


def test_first_fill_downloads_the_requested_span_and_records_where_it_came_from(store, provider):
    bars = store.get_daily_history("AAPL", start=date(2024, 1, 2))
    expected = trading_days(date(2024, 1, 2), date(2026, 10, 1))
    assert list(bars["date"].dt.date) == expected
    cov = store.coverage("AAPL")
    # The provider rounds the period up ("10y" for 2.7 years), and the store keeps
    # everything it was given: the extra years are free, and the read is filtered.
    assert cov.first_date <= expected[0] and cov.last_date == date(2026, 10, 1)
    assert cov.bar_count >= len(expected)
    assert cov.covered_from <= expected[0]
    assert cov.source == "yfinance" and cov.adjusted is True
    assert cov.interval == "1d"
    assert len(provider.calls) == 1


def test_frame_has_the_provider_column_format_with_naive_midnight_dates(store):
    bars = store.get_daily_history("AAPL", start=date(2026, 9, 1))
    assert list(bars.columns) == BAR_COLUMNS
    assert bars["date"].dt.tz is None
    assert (bars["date"] == bars["date"].dt.normalize()).all()
    assert all(bars[c].dtype == "float64" for c in BAR_COLUMNS[1:])


def test_rerunning_is_idempotent_and_costs_no_request(store, provider):
    store.ensure_history(["AAPL"], years=2)
    before = store.coverage("AAPL")
    calls = len(provider.calls)
    reports = store.ensure_history(["AAPL"], years=2)
    assert [r.status for r in reports] == ["up_to_date"]
    assert len(provider.calls) == calls
    assert store.coverage("AAPL").bar_count == before.bar_count


def test_only_the_missing_tail_is_fetched_later(store, provider, clock):
    store.ensure_history(["AAPL"], years=3)
    full = store.get_daily_history("AAPL", refresh="never")
    provider.calls.clear()

    clock.now = datetime(2026, 10, 7, 22, 0, tzinfo=timezone.utc)  # the following Wednesday evening
    report = store.fill("AAPL")
    assert report.status == "extended"
    assert provider.calls == [("AAPL", "1mo")], "a few days' gap must not trigger a multi-year download"
    new_days = trading_days(date(2026, 10, 2), date(2026, 10, 7))
    assert report.bars_added == len(new_days)

    now_bars = store.get_daily_history("AAPL", refresh="never")
    assert list(now_bars["date"].dt.date) == list(full["date"].dt.date) + new_days
    assert now_bars["date"].is_unique
    pd.testing.assert_frame_equal(now_bars.iloc[: len(full)].reset_index(drop=True), full)


def test_an_older_stretch_is_fetched_when_start_reaches_before_what_is_held(store, provider):
    store.get_daily_history("AAPL", start=date(2025, 1, 2))
    provider.calls.clear()
    bars = store.get_daily_history("AAPL", start=date(2023, 1, 3))
    assert provider.calls and provider.calls[0][1] in ("5y", "10y", "max", "2y", "5y")
    assert bars["date"].min().date() == date(2023, 1, 3)
    assert list(bars["date"].dt.date) == trading_days(date(2023, 1, 3), date(2026, 10, 1))
    assert store.coverage("AAPL").covered_from == date(2023, 1, 3)
    provider.calls.clear()
    store.get_daily_history("AAPL", start=date(2023, 1, 3))
    assert provider.calls == [], "the head is covered now"


def test_a_late_listing_is_not_refetched_forever(tmp_path, clock):
    young = FakeProvider(clock, listed_from=date(2024, 6, 3))
    store = HistoryStore(tmp_path / "h.db", young, clock=clock)
    try:
        bars = store.get_daily_history("NEWCO", start=date(2020, 1, 2))
        assert bars["date"].min().date() == date(2024, 6, 3)
        assert store.coverage("NEWCO").covered_from == date(2020, 1, 2)
        young.calls.clear()
        store.get_daily_history("NEWCO", start=date(2020, 1, 2))
        assert young.calls == []
    finally:
        store.close()


def test_missing_days_stay_missing_and_nothing_is_filled_forward(tmp_path, clock):
    gappy = FakeProvider(clock)
    gappy.missing = {date(2026, 9, 15), date(2026, 9, 16)}
    store = HistoryStore(tmp_path / "h.db", gappy, clock=clock)
    try:
        bars = store.get_daily_history("AAPL", start=date(2026, 9, 1))
        days = set(bars["date"].dt.date)
        assert date(2026, 9, 15) not in days and date(2026, 9, 16) not in days
        assert len(bars) == len(trading_days(date(2026, 9, 1), date(2026, 10, 1))) - 2
        assert store.coverage("AAPL").max_gap_days == 4  # Mon 14th -> Thu 17th
    finally:
        store.close()


def test_half_formed_bars_are_dropped_not_repaired(tmp_path, clock):
    class Holey(FakeProvider):
        def get_ohlcv(self, symbol, period="6mo", interval="1d"):
            frame = super().get_ohlcv(symbol, period, interval)
            frame.loc[frame["date"].dt.date == date(2026, 9, 3), "close"] = np.nan
            frame.loc[frame["date"].dt.date == date(2026, 9, 4), "open"] = 0.0
            return frame

    holey = Holey(clock)
    store = HistoryStore(tmp_path / "h.db", holey, clock=clock)
    try:
        bars = store.get_daily_history("AAPL", start=date(2026, 9, 1))
        assert len(bars) == len(trading_days(date(2026, 9, 1), date(2026, 10, 1))) - 2
        assert bars[["open", "high", "low", "close"]].notna().all().all()
    finally:
        store.close()


def test_todays_forming_bar_is_not_stored(tmp_path):
    clock = Clock(MID_SESSION)
    provider = FakeProvider(clock)
    store = HistoryStore(tmp_path / "h.db", provider, clock=clock)
    try:
        bars = store.get_daily_history("AAPL", start=date(2026, 9, 1))
        assert bars["date"].max().date() == date(2026, 9, 30), "a mid-session snapshot must not be frozen into history"
        clock.now = EVENING
        store.fill("AAPL")
        assert store.coverage("AAPL").last_date == date(2026, 10, 1), "the final bar arrives once the session is over"
    finally:
        store.close()


def test_a_failed_fetch_leaves_stored_data_and_reports_it(store, provider, clock):
    store.ensure_history(["AAPL"], years=1)
    held = store.get_daily_history("AAPL", refresh="never")
    clock.now = datetime(2026, 10, 7, 22, 0, tzinfo=timezone.utc)
    provider.fail = True

    bars = store.get_daily_history("AAPL")  # wants a newer tail; the provider is down
    pd.testing.assert_frame_equal(bars, held)
    report = store.last_report("AAPL")
    assert report.status == "failed" and "provider down" in report.error and not report.ok
    assert store.coverage("AAPL").last_date == date(2026, 10, 1)


def test_nothing_stored_and_a_failed_fetch_raises_rather_than_returning_an_empty_frame(store, provider):
    provider.fail = True
    with pytest.raises(HistoryUnavailableError):
        store.get_daily_history("AAPL")
    assert store.coverage("AAPL") is None


def test_a_bulk_run_reports_a_failure_and_carries_on(store, provider):
    real_get = provider.get_ohlcv

    def flaky(symbol, period="6mo", interval="1d"):
        if symbol == "BAD":
            provider.calls.append((symbol, period))
            raise AllProvidersFailedError("no such symbol")
        return real_get(symbol, period, interval)

    provider.get_ohlcv = flaky
    seen: list[tuple[int, int, str]] = []
    reports = store.ensure_history(
        ["AAPL", "BAD", "SPY"], years=1, sleep=lambda s: None, progress=lambda i, n, r: seen.append((i, n, r.symbol))
    )
    assert [r.status for r in reports] == ["filled", "failed", "filled"]
    assert seen == [(1, 3, "AAPL"), (2, 3, "BAD"), (3, 3, "SPY")]
    assert store.coverage("BAD") is None and store.coverage("SPY") is not None


def test_bulk_preload_paces_only_after_real_fetches(store):
    store.ensure_history(["AAPL"], years=1)  # AAPL is already held
    sleeps: list[float] = []
    store.ensure_history(["AAPL", "SPY", "^VIX"], years=1, pace_seconds=2.5, sleep=sleeps.append)
    # AAPL: up to date, no wait. SPY fetched -> wait before ^VIX. ^VIX is last -> no wait after it.
    assert sleeps == [2.5]


def test_end_bounds_what_comes_back_so_a_run_cannot_see_the_future(store, provider):
    store.get_daily_history("AAPL", start=date(2024, 1, 2))
    provider.calls.clear()
    bars = store.get_daily_history("AAPL", start=date(2024, 1, 2), end=date(2025, 6, 30))
    assert bars["date"].max().date() <= date(2025, 6, 30)
    assert bars["date"].max().date() == max(trading_days(date(2025, 6, 2), date(2025, 6, 30)))
    assert provider.calls == [], "a historical window already held needs no fetch"


def test_refresh_never_reads_the_disk_only(tmp_path, clock):
    def exploding_factory():
        raise AssertionError("the provider must not even be built")

    store = HistoryStore(tmp_path / "h.db", exploding_factory, clock=clock)
    try:
        assert store.get_daily_history("AAPL", refresh="never").empty
    finally:
        store.close()


def test_inside_a_simulated_moment_the_end_is_clamped_to_that_moment(store):
    store.get_daily_history("AAPL", start=date(2026, 8, 1))
    # 2026-09-15 at 20:00 UTC = 16:00 NY: Sep 15's bar is not yet final (30 min grace), Sep 14's is.
    with as_of(datetime(2026, 9, 15, 20, 0)):
        bars = store.get_daily_history("AAPL", refresh="never")
        assert bars["date"].max().date() == date(2026, 9, 14)
        later = store.get_daily_history("AAPL", end=date(2026, 9, 30), refresh="never")
        assert later["date"].max().date() == date(2026, 9, 14), "asking for the future inside a simulation is clamped"
    with as_of(datetime(2026, 9, 15, 21, 0)):
        assert store.get_daily_history("AAPL", refresh="never")["date"].max().date() == date(2026, 9, 15)


def test_a_rebased_history_is_refetched_not_spliced(store, provider, clock):
    """A dividend shifts every older adjusted close. Joining new bars onto the old
    basis would leave a seam in the series; the store must notice and start over."""
    store.get_daily_history("AAPL", start=date(2025, 1, 2))
    clock.now = datetime(2026, 10, 7, 22, 0, tzinfo=timezone.utc)
    provider.rebase_before = date(2026, 10, 5)  # ex-dividend day
    provider.rebase_factor = 0.98
    report = store.fill("AAPL")
    assert report.status == "rebuilt"
    bars = store.get_daily_history("AAPL", refresh="never")
    expected_old = FakeProvider._close("AAPL", date(2025, 3, 3)) * 0.98
    got = bars.loc[bars["date"] == pd.Timestamp("2025-03-03"), "close"].iloc[0]
    assert got == pytest.approx(expected_old)
    assert bars["date"].max().date() == date(2026, 10, 7)
    assert list(bars["date"].dt.date) == trading_days(bars["date"].min().date(), date(2026, 10, 7))
    # no seam: every stored close is on the new basis, before and after the ex-date
    recent = bars.loc[bars["date"] == pd.Timestamp("2026-10-07"), "close"].iloc[0]
    assert recent == pytest.approx(FakeProvider._close("AAPL", date(2026, 10, 7)))


def test_a_different_provider_is_not_spliced_onto_stored_bars(store, provider, clock):
    store.get_daily_history("AAPL", start=date(2025, 1, 2))
    held = store.get_daily_history("AAPL", refresh="never")
    clock.now = datetime(2026, 10, 7, 22, 0, tzinfo=timezone.utc)
    provider.name = "nasdaq"  # Yahoo is down; Nasdaq answers with unadjusted bars
    report = store.fill("AAPL")
    assert report.status == "skipped_source_changed" and "nasdaq" in report.error
    pd.testing.assert_frame_equal(store.get_daily_history("AAPL", refresh="never"), held)

    forced = store.fill("AAPL", force=True)
    assert forced.status == "filled" and store.coverage("AAPL").source == "nasdaq"
    assert store.coverage("AAPL").adjusted is False
    assert store.coverage("AAPL").last_date == date(2026, 10, 7)


def test_force_replaces_everything(store, provider):
    store.get_daily_history("AAPL", start=date(2025, 6, 2))
    provider.calls.clear()
    bars = store.get_daily_history("AAPL", refresh="force")
    assert provider.calls, "force must hit the provider"
    assert store.last_report("AAPL").status == "filled"
    assert bars["date"].is_unique


def test_a_provider_that_lags_is_not_asked_again_until_a_newer_bar_can_exist(tmp_path, clock):
    lagging = FakeProvider(clock)
    lagging.lag_days = 1  # today's final bar is not out yet
    store = HistoryStore(tmp_path / "h.db", lagging, clock=clock)
    try:
        store.get_daily_history("AAPL", start=date(2026, 9, 1))
        assert store.coverage("AAPL").last_date == date(2026, 9, 30)
        lagging.calls.clear()
        store.get_daily_history("AAPL")
        store.get_daily_history("AAPL")
        assert lagging.calls == [], "we already looked after that bar was due; asking again is pointless"
        clock.now += timedelta(days=1)  # the next session's bar is now due
        store.get_daily_history("AAPL")
        assert len(lagging.calls) == 1
    finally:
        store.close()


def test_coverage_and_summary(store):
    assert store.coverage("NOPE") is None
    store.ensure_history(["AAPL", "SPY"], years=1, sleep=lambda s: None)
    summary = store.summary()
    assert summary.symbols == 2
    assert summary.bars == store.coverage("AAPL").bar_count + store.coverage("SPY").bar_count
    assert summary.last_date == date(2026, 10, 1) and summary.file_bytes > 0
    assert {s for s, _ in summary.stalest} == {"AAPL", "SPY"}


def test_symbols_are_case_insensitive_and_spaces_are_trimmed(store, provider):
    store.get_daily_history(" aapl ", start=date(2026, 9, 1))
    assert store.coverage("AAPL") is not None
    assert provider.calls[0][0] == "AAPL"


def test_only_daily_bars_are_supported_for_now(store):
    with pytest.raises(NotImplementedError):
        store.get_daily_history("AAPL", interval="1h")
    with pytest.raises(NotImplementedError):
        store.coverage("AAPL", interval="5m")
    with pytest.raises(ValueError):
        store.get_daily_history("AAPL", refresh="sometimes")  # type: ignore[arg-type]


def test_data_survives_reopening_the_file(tmp_path, clock, provider):
    path = tmp_path / "h.db"
    first = HistoryStore(path, provider, clock=clock)
    first.get_daily_history("AAPL", start=date(2025, 1, 2))
    held = first.get_daily_history("AAPL", refresh="never")
    first.close()
    provider.calls.clear()
    second = HistoryStore(path, provider, clock=clock)
    try:
        pd.testing.assert_frame_equal(second.get_daily_history("AAPL"), held)
        assert provider.calls == []
    finally:
        second.close()


# ---- helpers ----------------------------------------------------------------


def test_normalize_bars_makes_naive_dates_sorts_dedupes_and_validates():
    raw = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-03", "2026-09-01", "2026-09-01", "2026-09-02"]).tz_localize("America/New_York"),
            "open": [3.0, 1.0, 1.1, -2.0],
            "high": [3.5, 1.5, 1.6, 2.5],
            "low": [2.5, 0.5, 0.6, 1.5],
            "close": [3.2, 1.2, 1.3, 2.2],
            "volume": [30, 10, 11, 20],
        }
    )
    out = normalize_bars(raw, now=EVENING)
    assert list(out["date"].dt.date) == [date(2026, 9, 1), date(2026, 9, 3)]  # -2.0 open dropped; duplicate keeps last
    assert out["date"].dt.tz is None
    assert out.loc[0, "close"] == 1.3


def test_normalize_bars_rejects_frames_without_the_provider_columns():
    with pytest.raises(ValueError):
        normalize_bars(pd.DataFrame({"date": [1], "close": [2]}))


@pytest.mark.parametrize(
    "days,period",
    [(1, "5d"), (7, "5d"), (8, "1mo"), (40, "3mo"), (365, "1y"), (3652, "10y"), (3700, "max"), (20000, "max")],
)
def test_period_for_days_picks_the_smallest_period_that_reaches_back_far_enough(days, period):
    assert period_for_days(days) == period


def test_fill_report_ok_flag():
    assert FillReport("A", "extended").ok and FillReport("A", "up_to_date").ok
    assert not FillReport("A", "failed").ok and not FillReport("A", "skipped_source_changed").ok
