"""The backtest data provider: what a trader could see at the simulated moment, and nothing else."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from app.backtest.calendar import close_moment, decision_moment
from app.backtest.data_provider import BacktestDataProvider, PriceBook
from app.data_providers.base import AllProvidersFailedError, QuoteData
from app.knowledge.point_in_time import LookAheadError, as_of
from tests.backtest_helpers import frame_from, trading_days_from

# 2024-10-01 is a Tuesday; the series runs well past it.
DAYS = trading_days_from(date(2022, 6, 1), 640)
DAY_INDEX = {d: i for i, d in enumerate(DAYS)}
TODAY = date(2024, 10, 2)  # a Wednesday: Mon 30 Sep and Tue 1 Oct are known, Wed is "today"


def _book(poison: dict[date, float] | None = None) -> PriceBook:
    count = len(DAYS)
    closes = 100 + np.arange(count, dtype=float)  # +1 a day: every number identifies its day
    opens = closes - 0.25
    frame = frame_from(DAYS, closes, opens=opens, highs=closes + 0.5, lows=closes - 0.5, volume=1000.0)
    frame.loc[:, "volume"] = 1000.0 + np.arange(count)
    for day, high in (poison or {}).items():
        frame.loc[frame["date"] == pd.Timestamp(day), "high"] = high
    frames = {"AAA": frame, "SPY": frame_from(DAYS, closes), "^VIX": frame_from(DAYS, np.full(count, 15.0))}
    return PriceBook.from_frames(frames)


@pytest.fixture()
def provider() -> BacktestDataProvider:
    return BacktestDataProvider(_book(), record_calls=True)


def _bar(symbol_frame: pd.DataFrame, day: date) -> pd.Series:
    return symbol_frame[symbol_frame["date"] == pd.Timestamp(day)].iloc[0]


def test_decision_moment_sees_bars_through_the_previous_close_only(provider):
    with as_of(decision_moment(TODAY)):
        bars = provider.get_ohlcv("AAA", period="1y", interval="1d")
    assert pd.Timestamp(bars["date"].iloc[-1]).date() == date(2024, 10, 1)
    assert pd.Timestamp(TODAY) not in set(bars["date"])
    # 1y back from the decision day, as a live provider counts it
    assert pd.Timestamp(bars["date"].iloc[0]).date() >= date(2023, 10, 2)
    assert 245 <= len(bars) <= 255


def test_close_moment_sees_the_days_own_bar(provider):
    with as_of(close_moment(TODAY)):
        bars = provider.get_ohlcv("AAA", period="1mo", interval="1d")
    assert pd.Timestamp(bars["date"].iloc[-1]).date() == TODAY


def test_quote_at_the_decision_moment_is_the_open_with_zero_volume(provider):
    book = provider._book.series["AAA"]
    today_bar, yesterday = book.bar_index_on(TODAY), book.bar_index_on(date(2024, 10, 1))
    with as_of(decision_moment(TODAY)):
        quote = provider.get_quote("AAA")
    assert isinstance(quote, QuoteData)
    assert quote.price == book.opens[today_bar]
    assert quote.change_pct_24h == pytest.approx((book.opens[today_bar] / book.closes[yesterday] - 1) * 100)
    assert quote.volume == 0.0
    # mean of the 20 known bars ending yesterday
    assert quote.avg_volume_20d == pytest.approx(book.volumes[yesterday - 19 : yesterday + 1].mean())


def test_quote_after_the_close_is_the_close_with_the_days_volume(provider):
    book = provider._book.series["AAA"]
    today_bar = book.bar_index_on(TODAY)
    with as_of(close_moment(TODAY)):
        quote = provider.get_quote("AAA")
    assert quote.price == book.closes[today_bar]
    assert quote.volume == book.volumes[today_bar]
    assert quote.change_pct_24h == pytest.approx((book.closes[today_bar] / book.closes[today_bar - 1] - 1) * 100)


def test_quote_before_the_open_is_the_previous_close(provider):
    book = provider._book.series["AAA"]
    before_open = datetime(2024, 10, 2, 12, 0)  # 08:00 ET
    with as_of(before_open):
        quote = provider.get_quote("AAA")
    assert quote.price == book.closes[book.bar_index_on(date(2024, 10, 1))]


def test_provider_refuses_to_answer_outside_a_simulated_moment(provider):
    with pytest.raises(LookAheadError):
        provider.get_ohlcv("AAA")
    with pytest.raises(LookAheadError):
        provider.get_quote("AAA")


def test_nothing_returned_is_final_after_the_asked_moment(provider):
    """Every recorded answer, at both phases of many days, ends on a bar that was final at that moment."""
    from app.markets import is_daily_bar_final

    for day in DAYS[300:340]:
        for moment in (decision_moment(day), close_moment(day)):
            with as_of(moment):
                provider.get_ohlcv("AAA", period="6mo", interval="1d")
                provider.get_ohlcv("AAA", period="2y", interval="1wk")
                provider.get_quote("AAA")
    assert provider.calls
    for call in provider.calls:
        assert is_daily_bar_final("SPY", call.last_bar_day, call.as_of), call


def _week_frame(provider, moment, symbol="AAA"):
    with as_of(moment):
        return provider.get_ohlcv(symbol, period="2y", interval="1wk")


def test_weekly_bar_in_progress_is_built_from_known_days_only():
    # Wed 2 Oct 2024: Mon 30 Sep and Tue 1 Oct are known. Thursday's high is poisoned: it must not show.
    book = _book(poison={date(2024, 10, 3): 10_000.0})  # the future
    series = book.series["AAA"]
    provider = BacktestDataProvider(book)
    weekly = _week_frame(provider, decision_moment(date(2024, 10, 2)))
    last = weekly.iloc[-1]
    monday, tuesday = series.bar_index_on(date(2024, 9, 30)), series.bar_index_on(date(2024, 10, 1))
    assert pd.Timestamp(last["date"]).date() == date(2024, 9, 30)  # the week's Monday
    assert last["open"] == series.opens[monday]
    assert last["close"] == series.closes[tuesday]
    assert last["high"] == max(series.highs[monday], series.highs[tuesday])
    assert last["low"] == min(series.lows[monday], series.lows[tuesday])
    assert last["volume"] == pytest.approx(series.volumes[monday] + series.volumes[tuesday])
    assert last["high"] < 10_000


def test_weekly_on_a_monday_decision_ends_with_the_complete_previous_week(provider):
    series = provider._book.series["AAA"]
    weekly = _week_frame(provider, decision_moment(date(2024, 10, 7)))  # Monday
    last = weekly.iloc[-1]
    assert pd.Timestamp(last["date"]).date() == date(2024, 9, 30)
    friday = series.bar_index_on(date(2024, 10, 4))
    assert last["close"] == series.closes[friday]
    assert last["high"] == max(series.highs[series.bar_index_on(date(2024, 9, 30)) : friday + 1])


def test_weekly_after_friday_close_includes_that_complete_week(provider):
    series = provider._book.series["AAA"]
    weekly = _week_frame(provider, close_moment(date(2024, 10, 4)))
    assert weekly.iloc[-1]["close"] == series.closes[series.bar_index_on(date(2024, 10, 4))]


def test_weekly_period_is_about_two_years_of_weeks(provider):
    weekly = _week_frame(provider, decision_moment(TODAY))
    assert 100 <= len(weekly) <= 106
    assert list(weekly.columns) == ["date", "open", "high", "low", "close", "volume"]


def test_unsupported_requests_fail_like_a_provider_with_no_data(provider):
    with as_of(decision_moment(TODAY)):
        with pytest.raises(AllProvidersFailedError):
            provider.get_ohlcv("AAA", period="5d", interval="1h")  # no intraday bars
        with pytest.raises(AllProvidersFailedError):
            provider.get_ohlcv("NOPE")  # no stored history
        with pytest.raises(AllProvidersFailedError):
            provider.get_ohlcv("BTC-USD")  # crypto is out of scope
        with pytest.raises(AllProvidersFailedError):
            provider.get_quote("NOPE")


def test_everything_a_price_history_cannot_know_fails_the_way_the_composite_does(provider):
    with as_of(decision_moment(TODAY)):
        with pytest.raises(AllProvidersFailedError):
            provider.get_company_overview("AAA")
        with pytest.raises(AllProvidersFailedError):
            provider.get_financials("AAA")
        with pytest.raises(AllProvidersFailedError):
            provider.get_news("AAA")
        assert provider.get_earnings_date("AAA") is None
        assert provider.get_earnings_estimate("AAA") is None
        assert provider.get_earnings_history("AAA") == []
        assert provider.get_options_summary("AAA") is None
        assert provider.get_insider_activity("AAA") is None


def test_dated_sources_plug_in_without_touching_the_provider():
    seen: list[tuple[str, datetime]] = []

    class Sources:
        def earnings_date(self, symbol, moment):
            seen.append((symbol, moment))
            return date(2024, 10, 20)

    provider = BacktestDataProvider(_book(), dated_sources=Sources())
    moment = decision_moment(TODAY)
    with as_of(moment):
        assert provider.get_earnings_date("AAA") == date(2024, 10, 20)
        assert provider.get_options_summary("AAA") is None  # the source has no such method: stays unavailable
    assert seen == [("AAA", moment)]


def test_missing_history_is_reported_as_skipped():
    book = PriceBook.from_frames({"AAA": frame_from(DAYS[:30], np.arange(30) + 10.0), "EMPTY": pd.DataFrame()})
    assert "AAA" in book and "EMPTY" not in book
    assert book.skipped == {"EMPTY": "no stored price history"}
