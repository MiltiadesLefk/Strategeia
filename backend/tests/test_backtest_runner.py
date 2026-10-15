"""The backtest day loop on hand-built bars where every number can be checked by hand."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from sqlmodel import Session

from app.backtest import runner
from app.backtest.calendar import close_moment, decision_moment, trading_days
from app.backtest.data_provider import BacktestDataProvider, PriceBook
from app.backtest.params import BacktestInputError, BacktestParams, SettingsOverrides, effective_settings
from app.config import AppSettings
from app.knowledge.point_in_time import as_of
from app.markets import is_daily_bar_final, to_market_time
from tests.backtest_helpers import flat_series, frame_from, standard_book, trading_days_from, wiggly_uptrend

SLIPPAGE_BPS = 10.0
STARTING_CASH = 100_000.0
DAYS = trading_days_from(date(2024, 1, 2), 330)
INDEX = {d: i for i, d in enumerate(DAYS)}
START, END = DAYS[250], DAYS[280]  # 2024-12-30 .. 2025-02-12: a first trade enters and exits inside it
# +1% a day with a -1% day every seventh: a clean, strong uptrend the rules read as a long every few days
CLOSES = wiggly_uptrend(330, daily=0.01, dip=-0.01)


def settings_for(**overrides) -> AppSettings:
    return effective_settings(AppSettings(), SettingsOverrides(**{"slippage_bps": SLIPPAGE_BPS, **overrides}))


def params_for(symbols=("AAA",), start=START, end=END, **kwargs) -> BacktestParams:
    return BacktestParams(symbols=list(symbols), start=start, end=end, **kwargs)


@pytest.fixture(scope="module")
def book() -> PriceBook:
    return standard_book(DAYS, {"AAA": CLOSES})


@pytest.fixture(scope="module")
def result(book):
    return runner.run_backtest(params_for(), settings_for(), book)


def opens_of(book: PriceBook, symbol: str = "AAA") -> np.ndarray:
    return book.series[symbol].opens


# --------------------------------------------------------------------- (a) hand-checked trade


def test_a_clean_uptrend_gives_one_hold_buy_whose_numbers_match_the_bars(book, result):
    series = book.series["AAA"]
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade["direction"] == "long" and trade["status"] == "open"
    entry = INDEX[trade["entry_date"]]

    # entry: the entry day's OPEN plus 10 bps of slippage, at 09:45 ET (14:45 UTC in winter)
    assert trade["entry_price"] == pytest.approx(series.opens[entry] * (1 + SLIPPAGE_BPS / 10_000))
    assert trade["entry_at"] == decision_moment(trade["entry_date"])
    assert trade["planned_entry_price"] == pytest.approx(series.opens[entry])
    # a 4.5% slice of the account, with the wider of the 25% stop and the 200-day average as the disaster stop
    assert trade["shares"] == int(STARTING_CASH * 0.045 / series.opens[entry])
    ma200 = series.closes[entry - 200 : entry].mean()
    assert trade["stop_loss"] == pytest.approx(min(series.opens[entry] * 0.75, ma200))
    assert trade["tp1"] == 0.0 and trade["realized_r"] is None  # no target, and R is a swing idea
    # still held at the end: valued at the last close, return_pct is on cost
    assert trade["close_reason"] == "open_at_end"
    cost = trade["entry_price"] * trade["shares"]
    assert trade["return_pct"] == pytest.approx(trade["realized_pnl"] / cost * 100)
    assert trade["return_pct"] > 0
    assert trade["confidence_points"] is None and trade["scores"] == {}


def test_the_equity_curve_matches_cash_plus_shares_at_each_close(book, result):
    series = book.series["AAA"]
    trade = result.trades[0]
    entry = INDEX[trade["entry_date"]]
    cash_after_entry = STARTING_CASH - trade["shares"] * trade["entry_price"]
    by_day = {point["day"]: point for point in result.equity}
    assert set(by_day) == set(trading_days(START, END))
    for index in range(INDEX[START], INDEX[END] + 1):
        point = by_day[DAYS[index]]
        if index < entry:
            assert (point["equity"], point["cash"], point["open_positions"]) == (STARTING_CASH, STARTING_CASH, 0)
        else:  # an open long is valued at THAT DAY'S CLOSE
            assert point["equity"] == pytest.approx(cash_after_entry + trade["shares"] * series.closes[index]), index
            assert point["cash"] == pytest.approx(cash_after_entry) and point["open_positions"] == 1


def test_a_crash_through_the_disaster_stop_closes_at_that_days_close(book, result):
    entry = INDEX[result.trades[0]["entry_date"]]
    closes = CLOSES.copy()
    opens = np.r_[closes[0], closes[:-1]]
    crash_day = entry + 3
    closes[crash_day:] *= 0.4
    opens[crash_day:] *= 0.4  # a gap far below the stop that never recovers
    crashed = PriceBook.from_frames(
        {
            "AAA": frame_from(DAYS, closes, opens=opens),
            "SPY": frame_from(DAYS, wiggly_uptrend(330, 400.0, 0.002)),
            "^VIX": frame_from(DAYS, flat_series(330, 15.0)),
        }
    )
    run = runner.run_backtest(params_for(), settings_for(), crashed)
    trade = run.trades[0]
    assert trade["entry_date"] == DAYS[entry] and trade["entry_price"] == pytest.approx(result.trades[0]["entry_price"])
    assert trade["status"] == "closed" and trade["close_reason"] == "disaster_stop"
    assert trade["exit_date"] == DAYS[crash_day] and trade["exit_at"] == close_moment(DAYS[crash_day])
    assert closes[crash_day] < trade["stop_loss"]
    assert trade["exit_price"] == pytest.approx(closes[crash_day] * (1 - SLIPPAGE_BPS / 10_000))  # the close, not the stop
    assert trade["return_pct"] < -25
    assert trade["return_pct"] == pytest.approx(trade["realized_pnl"] / (trade["entry_price"] * trade["shares"]) * 100)
    assert trade["realized_r"] is None
    assert run.summary["trade_count"] == 1 and run.summary["average_return_pct"] == pytest.approx(trade["return_pct"])


def test_a_stock_below_its_200_day_average_is_never_bought():
    flat = standard_book(DAYS, {"AAA": flat_series(330, 100.0)})  # price == average is not "above"
    run = runner.run_backtest(params_for(), settings_for(), flat)
    assert run.trades == [] and run.summary["evaluations"] > 0 and run.summary["executed"] == 0


def test_buy_only_dips_leaves_a_clean_uptrend_alone(book):
    run = runner.run_backtest(params_for(), settings_for().model_copy(update={"hold_buy_only_dips": True}), book)
    assert run.trades == [] and run.coverage["checks_run"][-1]["check"] == "dip"


def test_new_buys_per_day_are_capped_like_the_live_scan(result):
    symbols = ["AAA", "BBB", "CCC"]
    three = standard_book(DAYS, {name: CLOSES * (1 + 0.1 * i) for i, name in enumerate(symbols)})
    settings = settings_for().model_copy(update={"hold_max_new_per_scan": 1})
    run = runner.run_backtest(params_for(symbols=symbols), settings, three)
    entry_days = [t["entry_date"] for t in run.trades]
    assert len(entry_days) == 3 and len(set(entry_days)) == 3  # one a day, in list order
    assert [t["symbol"] for t in run.trades] == symbols


def test_the_backtest_and_the_live_scan_call_the_same_function(book, monkeypatch):
    """One per-symbol decision function (gate, dip rule, hold buy), used by run_hold_scan and by the runner."""
    from app.services import automation_service

    seen: list[tuple[str, bool]] = []
    real = automation_service.hold_scan_symbol

    def spy(settings, provider, session, engine, symbol, reason, *, price_only=False):
        seen.append((symbol, price_only))
        return real(settings, provider, session, engine, symbol, reason, price_only=price_only)

    monkeypatch.setattr(automation_service, "hold_scan_symbol", spy)
    run = runner.run_backtest(params_for(end=DAYS[262]), settings_for(), book)
    assert run.summary["evaluations"] == len(seen) > 0 and {price_only for _, price_only in seen} == {True}

    seen.clear()
    monkeypatch.setattr(automation_service, "get_default_watchlist", lambda n=50: ["AAA"])
    with Session(runner._new_scratch_db()) as session, as_of(decision_moment(DAYS[262])):
        automation_service.run_hold_scan(settings_for(), BacktestDataProvider(book), session)
    assert seen == [("AAA", False)]  # the live scan runs the full gate


# --------------------------------------------------------------------- (b) no look-ahead


def test_every_request_in_a_run_is_about_bars_final_at_that_moment(book):
    provider = BacktestDataProvider(book, record_calls=True)
    runner.run_backtest(params_for(), settings_for(), book, provider=provider)
    assert len(provider.calls) > 50
    moments = set()
    for call in provider.calls:
        assert is_daily_bar_final("SPY", call.last_bar_day, call.as_of), call
        market_day = to_market_time(call.as_of).date()
        if call.as_of == decision_moment(market_day):
            assert call.last_bar_day < market_day, call  # strictly before the decision day
        moments.add((market_day, call.as_of == decision_moment(market_day)))
    assert any(is_decision for _, is_decision in moments) and any(not is_decision for _, is_decision in moments)


def test_changing_the_future_cannot_change_the_past(book, result):
    """The same run on a book whose bars AFTER the last simulated day are replaced by nonsense must give
    identical trades and equity: nothing in the run can have looked past its own moment."""
    last = INDEX[END]
    rng = np.random.default_rng(7)
    closes = CLOSES.copy()
    closes[last + 1 :] = closes[last] * rng.uniform(0.2, 5.0, len(closes) - last - 1)
    opens = np.r_[closes[0], closes[:-1]]
    opens[: last + 1] = book.series["AAA"].opens[: last + 1]
    spy = wiggly_uptrend(330, 400.0, 0.002)
    wild_spy = spy.copy()
    wild_spy[last + 1 :] = spy[last] * rng.uniform(0.2, 5.0, len(spy) - last - 1)
    # the VIX before the end must match the baseline's flat 15 for a like-for-like comparison
    vix = np.full(330, 15.0)
    vix[last + 1 :] = rng.uniform(5.0, 80.0, 330 - last - 1)
    poisoned = PriceBook.from_frames(
        {
            "AAA": frame_from(DAYS, closes, opens=opens),
            "SPY": frame_from(DAYS, wild_spy),
            "^VIX": frame_from(DAYS, vix),
        }
    )
    again = runner.run_backtest(params_for(), settings_for(), poisoned)
    assert again.trades == result.trades
    assert again.equity == result.equity


# --------------------------------------------------------------------- (c) determinism


def test_same_inputs_give_identical_results(book, result):
    again = runner.run_backtest(params_for(), settings_for(), book)
    assert again.trades == result.trades
    assert again.equity == result.equity
    assert again.summary == result.summary


# --------------------------------------------------------------------- (d) it is the live code


def test_the_runner_uses_the_live_hold_scan_step_and_the_live_engine(book, monkeypatch):
    from app.portfolio import engine as live_engine
    from app.services import automation_service

    assert runner.automation_service is automation_service

    calls = {"open": 0, "mark": 0}
    real_open = live_engine.PaperTradingEngine.open_position
    real_mark = live_engine.PaperTradingEngine.mark_to_market

    def open_spy(self, plan, *args, **kwargs):
        calls["open"] += 1
        return real_open(self, plan, *args, **kwargs)

    def mark_spy(self, *args, **kwargs):
        calls["mark"] += 1
        return real_mark(self, *args, **kwargs)

    monkeypatch.setattr(live_engine.PaperTradingEngine, "open_position", open_spy)
    monkeypatch.setattr(live_engine.PaperTradingEngine, "mark_to_market", mark_spy)
    run = runner.run_backtest(params_for(end=DAYS[262]), settings_for(), book)
    assert calls["open"] == run.summary["executed"] > 0
    assert calls["mark"] == run.summary["days_simulated"]


# --------------------------------------------------------------------- (e) calendar


def test_holidays_are_skipped_and_an_early_close_moves_the_exit_scan(monkeypatch):
    days = trading_days_from(date(2023, 9, 1), 330)
    closes = wiggly_uptrend(330, daily=0.01, dip=-0.01)
    long_book = standard_book(days, {"AAA": closes})
    start, end = date(2024, 11, 25), date(2024, 12, 3)
    moments: list[datetime] = []
    real_as_of = runner.as_of

    def recording_as_of(moment):
        moments.append(moment)
        return real_as_of(moment)

    monkeypatch.setattr(runner, "as_of", recording_as_of)
    run = runner.run_backtest(params_for(start=start, end=end), settings_for(), long_book)

    simulated = [point["day"] for point in run.equity]
    assert date(2024, 11, 28) not in simulated  # Thanksgiving
    assert date(2024, 11, 29) in simulated  # the day after: open, but closes at 1:00 pm
    assert simulated == trading_days(start, end)

    seen = set(moments)
    # early-close day: the bar is final 30 minutes after 1:00 pm ET = 13:30 ET = 18:30 UTC
    assert close_moment(date(2024, 11, 29)).hour == 18 and close_moment(date(2024, 11, 29)).minute == 30
    assert close_moment(date(2024, 11, 29)) in seen
    # a normal day: 16:30 ET = 21:30 UTC in winter
    assert close_moment(date(2024, 11, 27)).hour == 21 and close_moment(date(2024, 11, 27)).minute == 30
    # no request is ever made for a holiday or a weekend moment
    for moment in seen:
        assert to_market_time(moment).date() in set(simulated)
    # nothing opens on a holiday
    assert all(trade["entry_date"] != date(2024, 11, 28) for trade in run.trades)


# --------------------------------------------------------------------- (f) isolation


def test_a_run_never_reads_the_saved_settings_never_messages_and_never_calls_an_llm(book, monkeypatch):
    from app import config
    from app.services import telegram_service

    def boom(*args, **kwargs):
        raise AssertionError("a backtest must not do this")

    monkeypatch.setattr(config, "load_app_settings", boom)
    monkeypatch.setattr(telegram_service, "send_message", boom)
    monkeypatch.setattr("app.llm_providers.factory.get_llm_provider", boom)
    loud = AppSettings(
        telegram_bot_token="123:secret", telegram_chat_id="42", llm_provider="claude_code_cli",
        finnhub_enabled=True, finnhub_api_key="k", auto_execute_trade_plans=False,
        auto_scan_enabled=True,
    )
    effective = effective_settings(loud, SettingsOverrides(slippage_bps=SLIPPAGE_BPS))
    assert (effective.telegram_bot_token, effective.telegram_chat_id) == ("", "")
    assert effective.llm_provider == "none"
    assert effective.auto_execute_trade_plans and not effective.finnhub_enabled and not effective.auto_scan_enabled
    assert loud.telegram_bot_token == "123:secret"  # the caller's settings are not modified

    run = runner.run_backtest(params_for(end=DAYS[262]), effective, book)
    assert run.summary["executed"] > 0


def test_a_run_does_not_touch_the_real_database(book, monkeypatch):
    import app.database as database

    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError("a backtest must not open the real database")

    monkeypatch.setattr(database, "engine", Forbidden())
    run = runner.run_backtest(params_for(end=DAYS[262]), settings_for(), book)
    assert run.summary["evaluations"] > 0


# --------------------------------------------------------------------- run-level rules


def test_slippage_override_changes_the_fill(book, result):
    free = runner.run_backtest(params_for(end=DAYS[262]), settings_for(slippage_bps=0.0), book)
    series = book.series["AAA"]
    trade = free.trades[0]
    assert trade["entry_price"] == pytest.approx(series.opens[INDEX[trade["entry_date"]]])


def test_decision_every_n_days_only_decides_on_those_days(book):
    every_day = runner.run_backtest(params_for(end=DAYS[262]), settings_for(), book)
    sparse = runner.run_backtest(params_for(end=DAYS[262], decision_every_n_days=5), settings_for(), book)
    assert sparse.summary["decision_days"] == -(-sparse.summary["days_simulated"] // 5)
    assert sparse.summary["decision_days"] < every_day.summary["decision_days"]
    assert sparse.summary["days_simulated"] == every_day.summary["days_simulated"]  # exits and equity are daily


def test_missing_benchmarks_and_missing_symbols_are_refused_with_a_clear_message(book):
    no_vix = PriceBook({k: v for k, v in book.series.items() if k != "^VIX"})
    with pytest.raises(BacktestInputError, match="VIX"):
        runner.run_backtest(params_for(), settings_for(), no_vix)
    with pytest.raises(BacktestInputError, match="none of the requested symbols"):
        runner.run_backtest(params_for(symbols=("ZZZ",)), settings_for(), book)


def test_a_symbol_without_history_is_reported_skipped(book):
    run = runner.run_backtest(params_for(symbols=("AAA", "ZZZ"), end=DAYS[262]), settings_for(), book)
    assert run.summary["symbols_with_data"] == 1 and run.summary["symbols_requested"] == 2
    assert run.summary["symbols_skipped"] == [{"symbol": "ZZZ", "reason": "no stored price history"}]


def test_a_young_listing_waits_for_its_warmup(book):
    young = standard_book(DAYS, {"AAA": CLOSES})
    young_frame = frame_from(DAYS[200:], CLOSES[200:])  # only 130 bars exist
    young.series["NEW"] = type(young.series["AAA"])("NEW", young_frame)
    run = runner.run_backtest(params_for(symbols=("NEW",), end=DAYS[262]), settings_for(), young)
    assert run.summary["evaluations"] == 0 and run.summary["skipped_warmup"] > 0


def test_cancel_stops_at_the_next_day_and_keeps_what_was_done(book):
    seen: list[date] = []
    run = runner.run_backtest(
        params_for(), settings_for(), book,
        progress=lambda done, total, day: seen.append(day),
        should_cancel=lambda: len(seen) >= 5,
    )
    assert run.status == "cancelled" and run.summary["days_simulated"] == 5 and run.summary["cancelled"] is True


def test_summary_numbers(book):
    crash = CLOSES.copy()
    crash[INDEX[START] + 8 :] *= 0.4
    result = runner.run_backtest(params_for(), settings_for(), standard_book(DAYS, {"AAA": crash}))
    summary = result.summary
    closed = [t for t in result.trades if t["status"] == "closed"]
    assert summary["trade_count"] == len(closed)
    assert summary["win_rate"] == sum(1 for t in closed if t["realized_pnl"] > 0) / len(closed)
    assert summary["average_r"] is None  # R is a swing idea
    assert summary["average_return_pct"] == pytest.approx(sum(t["return_pct"] for t in closed) / len(closed))
    assert summary["final_equity"] == result.equity[-1]["equity"]
    assert summary["total_return_pct"] == pytest.approx((summary["final_equity"] / STARTING_CASH - 1) * 100)
    assert summary["days_simulated"] == len(trading_days(START, END)) == len(result.equity)
    peak = STARTING_CASH
    worst = 0.0
    for point in result.equity:
        peak = max(peak, point["equity"])
        worst = max(worst, (peak - point["equity"]) / peak * 100)
    assert summary["max_drawdown_pct"] == pytest.approx(worst)
    assert summary["symbols_with_data"] == 1 and summary["symbols_skipped"] == []
    assert pd.Timestamp(summary["first_day"]).date() == START
