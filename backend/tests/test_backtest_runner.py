"""The backtest day loop on hand-built bars where every number can be checked by hand."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from sqlmodel import Session, select

from app.backtest import runner
from app.backtest.calendar import close_moment, decision_moment, trading_days
from app.backtest.data_provider import BacktestDataProvider, PriceBook
from app.backtest.params import BacktestInputError, BacktestParams, SettingsOverrides, effective_settings
from app.services.trade_plan_service import MAX_SCORE_FOR_CONFIDENCE
from app.config import AppSettings
from app.knowledge.point_in_time import as_of
from app.llm_providers.null_provider import NullLLMProvider
from app.markets import is_daily_bar_final, to_market_time
from app.portfolio.models import PaperPosition
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


def test_a_clean_uptrend_gives_one_long_whose_numbers_match_the_bars(book, result):
    series = book.series["AAA"]
    trade = result.trades[0]
    assert trade["direction"] == "long" and trade["status"] == "closed"
    entry = INDEX[trade["entry_date"]]

    # entry: the entry day's OPEN plus 10 bps of slippage, at 09:45 ET (14:45 UTC in winter)
    assert trade["entry_price"] == pytest.approx(series.opens[entry] * (1 + SLIPPAGE_BPS / 10_000))
    assert trade["entry_at"] == decision_moment(trade["entry_date"])
    assert trade["entry_at"].hour == 14 and trade["entry_at"].minute == 45
    # the plan was made from the previous close, and sized to risk 1% of the account
    assert trade["planned_entry_price"] == pytest.approx(series.closes[entry - 1])
    assert trade["stop_loss"] < trade["planned_entry_price"] < trade["tp1"]
    assert trade["shares"] == int(STARTING_CASH * 0.01 / (trade["planned_entry_price"] - trade["stop_loss"]))

    # exit: the first bar after the entry day whose range touches a level (the stop is checked first)
    expected_exit = None
    for j in range(entry + 1, len(DAYS)):
        if series.lows[j] <= trade["stop_loss"]:
            expected_exit = (j, "stop_hit")
            break
        if series.highs[j] >= trade["tp1"]:
            expected_exit = (j, "tp1_hit")
            break
    assert expected_exit is not None
    exit_index, reason = expected_exit
    assert trade["exit_date"] == DAYS[exit_index] and trade["close_reason"] == reason == "tp1_hit"
    # a take-profit is a resting limit: filled at its price (or the open, if the bar opened beyond it)
    assert trade["exit_price"] == pytest.approx(max(series.opens[exit_index], trade["tp1"]))
    assert trade["exit_at"] == close_moment(DAYS[exit_index])
    assert trade["holding_days"] == exit_index - entry

    pnl = (trade["exit_price"] - trade["entry_price"]) * trade["shares"]
    assert trade["realized_pnl"] == pytest.approx(pnl)
    assert trade["realized_r"] == pytest.approx(pnl / (abs(trade["entry_price"] - trade["stop_loss"]) * trade["shares"]))

    # the score that let it in: 3 technical + 2 weekly/SPY + 1 for the clean uptrend leading the market or its
    # volume trend (price-only evidence) = 6 points, above the 5-point bar
    assert trade["confidence_points"] == 6 and trade["confidence_points_max"] == MAX_SCORE_FOR_CONFIDENCE
    scores = trade["scores"]
    assert scores["technical_score"] + scores["market_confirmation_score"] + scores["relative_strength"] + scores["volume_trend"] == 6
    assert trade["scores"]["news_score"] == trade["scores"]["fundamental_score"] == 0


def test_the_equity_curve_matches_cash_plus_shares_at_each_close(book, result):
    series = book.series["AAA"]
    trade = result.trades[0]
    entry, exit_index = INDEX[trade["entry_date"]], INDEX[trade["exit_date"]]
    cash_after_entry = STARTING_CASH - trade["shares"] * trade["entry_price"]
    by_day = {point["day"]: point for point in result.equity}
    assert set(by_day) == set(trading_days(START, END))
    for index in range(INDEX[START], exit_index + 1):
        day = DAYS[index]
        if index < entry:
            expected_equity, expected_cash, held = STARTING_CASH, STARTING_CASH, 0
        elif index < exit_index:
            # an open long is valued at THAT DAY'S CLOSE, not at the entry price or the open
            expected_equity = cash_after_entry + trade["shares"] * series.closes[index]
            expected_cash, held = cash_after_entry, 1
        else:
            expected_equity = STARTING_CASH + trade["realized_pnl"]
            expected_cash, held = expected_equity, 0
        point = by_day[day]
        assert point["equity"] == pytest.approx(expected_equity), day
        assert point["cash"] == pytest.approx(expected_cash), day
        assert point["open_positions"] == held, day


def test_a_gap_through_the_stop_fills_at_the_open_not_at_the_stop(book, result):
    entry = INDEX[result.trades[0]["entry_date"]]
    closes = CLOSES.copy()
    opens = np.r_[closes[0], closes[:-1]]
    gap_day = entry + 2
    closes[gap_day:] *= 0.8
    opens[gap_day:] *= 0.8  # the market opens 20% lower, far below any stop
    gapped = PriceBook.from_frames(
        {
            "AAA": frame_from(DAYS, closes, opens=opens),
            "SPY": frame_from(DAYS, wiggly_uptrend(330, 400.0, 0.002)),
            "^VIX": frame_from(DAYS, flat_series(330, 15.0)),
        }
    )
    run = runner.run_backtest(params_for(), settings_for(), gapped)
    trade = run.trades[0]
    assert trade["entry_date"] == DAYS[entry] and trade["entry_price"] == pytest.approx(result.trades[0]["entry_price"])
    assert trade["exit_date"] == DAYS[gap_day] and trade["close_reason"] == "stop_hit"
    assert opens[gap_day] < trade["stop_loss"]
    assert trade["exit_price"] == pytest.approx(opens[gap_day] * (1 - SLIPPAGE_BPS / 10_000))  # a market order: slips
    assert trade["realized_r"] < -1  # worse than the planned 1R loss, as in real life


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


def test_the_runner_uses_the_live_scorer_and_the_live_engine(book, monkeypatch):
    from app.portfolio import engine as live_engine
    from app.services import trade_plan_service

    assert runner.generate_trade_plan is trade_plan_service.generate_trade_plan
    assert runner.PaperTradingEngine is live_engine.PaperTradingEngine

    calls = {"plans": 0, "open": 0, "mark": 0}
    real_generate = trade_plan_service.generate_trade_plan
    real_open = live_engine.PaperTradingEngine.open_position
    real_mark = live_engine.PaperTradingEngine.mark_to_market

    def generate_spy(*args, **kwargs):
        calls["plans"] += 1
        return real_generate(*args, **kwargs)

    def open_spy(self, plan):
        calls["open"] += 1
        return real_open(self, plan)

    def mark_spy(self, *args, **kwargs):
        calls["mark"] += 1
        return real_mark(self, *args, **kwargs)

    monkeypatch.setattr(runner, "generate_trade_plan", generate_spy)
    monkeypatch.setattr(live_engine.PaperTradingEngine, "open_position", open_spy)
    monkeypatch.setattr(live_engine.PaperTradingEngine, "mark_to_market", mark_spy)
    run = runner.run_backtest(params_for(end=DAYS[262]), settings_for(), book)
    assert calls["plans"] == run.summary["evaluations"] > 0
    assert calls["open"] == run.summary["executed"] > 0
    assert calls["mark"] == run.summary["days_simulated"]


def test_the_exit_engine_is_built_like_the_live_ones(book):
    """Same arguments as the portfolio router and the scheduler pass; only the clock and the daily-only
    exit rule differ."""
    from app.api.routers.portfolio import build_engine

    settings = settings_for(max_holding_days=7, max_concurrent_positions=3)
    provider = BacktestDataProvider(book)
    with Session(runner._new_scratch_db()) as session:
        ours = runner._build_engine(session, provider, settings)
        live = build_engine(session, provider, settings)
    ignored = {"_clock", "_intraday_exits"}
    assert {k: v for k, v in vars(ours).items() if k not in ignored} == {
        k: v for k, v in vars(live).items() if k not in ignored
    }


def test_slots_and_order_follow_the_live_auto_scan(result, monkeypatch):
    """One decision pass on the same day gives the same plans and the same executed symbols as the live
    run_auto_scan, with the position cap binding."""
    from app.services import automation_service

    symbols = ["AAA", "BBB", "CCC"]
    three = standard_book(DAYS, {name: CLOSES * (1 + 0.1 * i) for i, name in enumerate(symbols)})
    settings = settings_for(max_concurrent_positions=1)
    day = result.trades[0]["entry_date"]  # a day AAA signals; the others have the same shape, so they do too
    provider = BacktestDataProvider(three)

    def decide_with_runner():
        stats, errors, hist, first, info = runner.Counter(), [], runner.Counter(), {}, {}
        scratch = runner._new_scratch_db()
        with Session(scratch) as session:
            runner._decide(day, symbols, three, provider, NullLLMProvider(), session, settings, stats, errors, hist, first, info)
            held = sorted(p.symbol for p in session.exec(select(PaperPosition)).all())
        return stats, held

    stats, held = decide_with_runner()
    assert stats["plans"] >= 2, "the fixture should produce several tradeable plans on this day"

    monkeypatch.setattr(automation_service, "get_default_watchlist", lambda n=50: symbols)
    with Session(runner._new_scratch_db()) as session, as_of(decision_moment(day)):
        outcome = automation_service.run_auto_scan(settings, provider, NullLLMProvider(), session)
        live_held = sorted(p.symbol for p in session.exec(select(PaperPosition)).all())
    assert len(outcome.generated) == stats["plans"]
    assert len(outcome.no_trade) == stats["no_trade"]
    assert live_held == held and len(held) == 1  # the cap of one position let only the first signal in
    assert stats["plans_not_executed"] == stats["plans"] - 1


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
    from app.services import telegram_service, trade_plan_service

    def boom(*args, **kwargs):
        raise AssertionError("a backtest must not do this")

    monkeypatch.setattr(config, "load_app_settings", boom)
    monkeypatch.setattr(trade_plan_service, "load_app_settings", boom)
    monkeypatch.setattr(telegram_service, "send_message", boom)
    monkeypatch.setattr("app.llm_providers.factory.get_llm_provider", boom)
    notified: list[tuple[str, str]] = []
    monkeypatch.setattr(trade_plan_service, "notify_trade_plan", lambda token, chat, text: notified.append((token, chat)))
    providers_seen: list[str] = []
    real = trade_plan_service.generate_with_fallback

    def fallback_spy(provider, prompt, fallback_text, *args, **kwargs):
        providers_seen.append(provider.name)
        return real(provider, prompt, fallback_text, *args, **kwargs)

    monkeypatch.setattr(trade_plan_service, "generate_with_fallback", fallback_spy)

    loud = AppSettings(
        telegram_bot_token="123:secret", telegram_chat_id="42", llm_provider="claude_code_cli",
        ai_trading_overlay_enabled=True, finnhub_enabled=True, finnhub_api_key="k", auto_execute_trade_plans=False,
        auto_scan_enabled=True,
    )
    effective = effective_settings(loud, SettingsOverrides(slippage_bps=SLIPPAGE_BPS))
    assert (effective.telegram_bot_token, effective.telegram_chat_id) == ("", "")
    assert effective.llm_provider == "none" and not effective.ai_trading_overlay_enabled
    assert effective.auto_execute_trade_plans and not effective.finnhub_enabled and not effective.auto_scan_enabled
    assert loud.telegram_bot_token == "123:secret"  # the caller's settings are not modified

    run = runner.run_backtest(params_for(end=DAYS[262]), effective, book)
    assert run.summary["executed"] > 0
    assert notified and all(pair == ("", "") for pair in notified)
    assert providers_seen and set(providers_seen) == {"none"}


def test_a_run_does_not_touch_the_real_database(book, monkeypatch):
    import app.database as database

    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError("a backtest must not open the real database")

    monkeypatch.setattr(database, "engine", Forbidden())
    run = runner.run_backtest(params_for(end=DAYS[262]), settings_for(), book)
    assert run.summary["evaluations"] > 0


def test_macro_events_are_left_out_inside_a_simulation(monkeypatch):
    """The macro-event check reads today's real date; inside a simulated moment that would apply this
    week's releases to every past day, so the price-only backtest scores it 0."""
    from app.services import trade_plan_service

    def boom():
        raise AssertionError("macro proximity must not be read during a simulation")

    monkeypatch.setattr(trade_plan_service, "score_macro_event_proximity", boom)
    book = standard_book(DAYS, {"AAA": CLOSES})
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


def test_summary_numbers(book, result):
    summary = result.summary
    closed = [t for t in result.trades if t["status"] == "closed"]
    assert summary["trade_count"] == len(closed)
    assert summary["win_rate"] == sum(1 for t in closed if t["realized_pnl"] > 0) / len(closed)
    assert summary["average_r"] == pytest.approx(sum(t["realized_r"] for t in closed) / len(closed))
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
