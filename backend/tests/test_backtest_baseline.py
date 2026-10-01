"""The random-entry baseline: deterministic per seed, same levels and caps as the live plan, no look-ahead."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from sqlmodel import Session

from app.backtest import baseline, runner
from app.backtest.baseline import (
    MIN_SEEDS_FOR_READING,
    RandomEntryPolicy,
    entry_probability_of,
    place_in_distribution,
    read_baseline,
    run_baseline,
)
from app.backtest.calendar import decision_moment
from app.backtest.data_provider import BacktestDataProvider, PriceBook
from app.backtest.params import BacktestParams, SettingsOverrides, effective_settings
from app.config import AppSettings
from app.knowledge.point_in_time import as_of
from app.markets import is_daily_bar_final, to_market_time
from tests.backtest_helpers import frame_from, standard_book, trading_days_from, wiggly_uptrend

DAYS = trading_days_from(date(2024, 1, 2), 330)
INDEX = {d: i for i, d in enumerate(DAYS)}
START, END = DAYS[250], DAYS[280]
CLOSES = wiggly_uptrend(330, daily=0.01, dip=-0.01)
SYMBOLS = ["AAA", "BBB", "CCC"]


def settings_for(**overrides) -> AppSettings:
    return effective_settings(AppSettings(), SettingsOverrides(**{"slippage_bps": 10.0, **overrides}))


def params_for(symbols=tuple(SYMBOLS), **kwargs) -> BacktestParams:
    return BacktestParams(symbols=list(symbols), start=START, end=END, **kwargs)


@pytest.fixture(scope="module")
def book() -> PriceBook:
    return standard_book(DAYS, {name: CLOSES * (1 + 0.1 * i) for i, name in enumerate(SYMBOLS)})


def random_run(book, seed=1, probability=0.3, long_probability=0.5, settings=None, **kwargs):
    policy = RandomEntryPolicy(seed, probability, long_probability)
    return runner.run_backtest(params_for(), settings or settings_for(), book, entry_policy=policy, **kwargs)


# ------------------------------------------------------------------ the draw


def test_the_draw_is_a_pure_function_of_seed_symbol_and_day():
    policy = RandomEntryPolicy(7, 0.3)
    forward = [policy.draw("AAA", d) for d in DAYS[:50]]
    backward = [policy.draw("AAA", d) for d in reversed(DAYS[:50])][::-1]
    assert forward == backward  # not a stream: asking in another order gives the same answers
    assert forward == [RandomEntryPolicy(7, 0.3).draw("AAA", d) for d in DAYS[:50]]
    assert forward != [RandomEntryPolicy(8, 0.3).draw("AAA", d) for d in DAYS[:50]]
    assert forward != [policy.draw("BBB", d) for d in DAYS[:50]]


def test_the_draw_enters_at_the_given_rate_with_even_odds_of_long_and_short():
    policy = RandomEntryPolicy(1, 0.3)
    draws = [policy.draw(f"S{i}", d) for i in range(10) for d in DAYS[:200]]
    entered = [d for d in draws if d is not None]
    assert len(entered) / len(draws) == pytest.approx(0.3, abs=0.03)
    assert sum(1 for d in entered if d == "long") / len(entered) == pytest.approx(0.5, abs=0.05)
    assert all(RandomEntryPolicy(1, 0.0).draw("AAA", d) is None for d in DAYS[:100])
    assert all(RandomEntryPolicy(1, 1.0).draw("AAA", d) is not None for d in DAYS[:100])
    assert {RandomEntryPolicy(1, 1.0, 1.0).draw("AAA", d) for d in DAYS[:100]} == {"long"}
    with pytest.raises(ValueError):
        RandomEntryPolicy(1, 1.5)


# ------------------------------------------------------------------ determinism


def test_a_seed_always_gives_the_same_run_and_other_seeds_give_other_runs(book):
    first = random_run(book, seed=1)
    again = random_run(book, seed=1)
    assert first.trades and first.trades == again.trades and first.equity == again.equity
    other = random_run(book, seed=2)
    assert [(t["symbol"], t["entry_date"], t["direction"]) for t in other.trades] != [
        (t["symbol"], t["entry_date"], t["direction"]) for t in first.trades
    ]


def test_random_runs_never_call_the_live_scorer(book, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("the live scorer must not decide a random entry")

    monkeypatch.setattr(runner, "generate_trade_plan", boom)
    assert random_run(book, seed=3).trades


def test_no_draw_means_no_trades_and_every_draw_means_trades_up_to_the_caps(book):
    assert random_run(book, probability=0.0).trades == []
    busy = random_run(book, probability=1.0, settings=settings_for(max_concurrent_positions=2))
    assert busy.trades
    assert max(p["open_positions"] for p in busy.equity) <= 2  # the live cap still binds


# ------------------------------------------------------------------ same levels, same engine


def test_a_random_entry_gets_exactly_the_stop_target_and_size_the_live_plan_gets(book):
    live = runner.run_backtest(params_for(symbols=("AAA",)), settings_for(), book)
    trade = live.trades[0]
    assert trade["direction"] == "long"
    provider = BacktestDataProvider(book)
    policy = RandomEntryPolicy(1, 1.0, long_probability=1.0)  # always enters, always long
    with Session(runner._new_scratch_db()) as session, as_of(decision_moment(trade["entry_date"])):
        response = policy("AAA", trade["entry_date"], provider, session, settings_for(), allow_auto_execute=False)
    assert response.direction == "long" and response.status == "pending"
    assert response.entry == pytest.approx(trade["planned_entry_price"])
    assert response.stop == pytest.approx(trade["stop_loss"])
    assert response.tp1 == pytest.approx(trade["tp1"])
    assert response.suggested_shares == trade["shares"]


def test_the_levels_come_from_the_same_functions_the_live_plan_calls():
    from app.services import trade_plan_service

    assert baseline._derive_entry_and_stop is trade_plan_service._derive_entry_and_stop
    assert baseline.derive_targets.__module__ == "app.risk.position_sizing"
    assert baseline.calculate_position_size.__module__ == "app.risk.position_sizing"


def test_random_runs_go_through_the_live_exit_engine(book, monkeypatch):
    from app.portfolio import engine as live_engine

    calls = {"open": 0, "mark": 0}
    real_open, real_mark = live_engine.PaperTradingEngine.open_position, live_engine.PaperTradingEngine.mark_to_market

    def open_spy(self, plan):
        calls["open"] += 1
        return real_open(self, plan)

    def mark_spy(self, *args, **kwargs):
        calls["mark"] += 1
        return real_mark(self, *args, **kwargs)

    monkeypatch.setattr(live_engine.PaperTradingEngine, "open_position", open_spy)
    monkeypatch.setattr(live_engine.PaperTradingEngine, "mark_to_market", mark_spy)
    run = random_run(book, seed=4)
    assert calls["open"] == run.summary["executed"] > 0
    assert calls["mark"] == run.summary["days_simulated"]
    # exits are the engine's: every closed trade ended on a stop, a target or the time limit
    assert {t["close_reason"] for t in run.trades} <= {"stop_hit", "tp1_hit", "time_exit", "open_at_end"}


# ------------------------------------------------------------------ no look-ahead


def test_every_request_a_random_run_makes_is_about_bars_final_at_that_moment(book):
    provider = BacktestDataProvider(book, record_calls=True)
    random_run(book, seed=5, provider=provider)
    assert len(provider.calls) > 50
    for call in provider.calls:
        assert is_daily_bar_final("SPY", call.last_bar_day, call.as_of), call
        market_day = to_market_time(call.as_of).date()
        if call.as_of == decision_moment(market_day):
            assert call.last_bar_day < market_day, call


def test_changing_the_future_cannot_change_a_random_run(book):
    last = INDEX[END]
    rng = np.random.default_rng(9)
    frames = {}
    for i, name in enumerate(SYMBOLS):
        closes = CLOSES * (1 + 0.1 * i)
        poisoned = closes.copy()
        poisoned[last + 1 :] = closes[last] * rng.uniform(0.2, 5.0, len(closes) - last - 1)
        opens = np.r_[poisoned[0], poisoned[:-1]]
        opens[: last + 1] = book.series[name].opens[: last + 1]
        frames[name] = frame_from(DAYS, poisoned, opens=opens)
    spy = wiggly_uptrend(330, 400.0, 0.002)
    wild_spy = spy.copy()
    wild_spy[last + 1 :] = spy[last] * rng.uniform(0.2, 5.0, 330 - last - 1)
    vix = np.full(330, 15.0)
    vix[last + 1 :] = rng.uniform(5.0, 80.0, 330 - last - 1)
    frames["SPY"], frames["^VIX"] = frame_from(DAYS, wild_spy), frame_from(DAYS, vix)
    clean = random_run(book, seed=6)
    poisoned_run = random_run(PriceBook.from_frames(frames), seed=6)
    assert clean.trades and poisoned_run.trades == clean.trades and poisoned_run.equity == clean.equity


# ------------------------------------------------------------------ the entry rate and the seeds


def test_the_entry_rate_is_the_real_runs_plans_per_evaluation():
    assert entry_probability_of({"plans": 10, "evaluations": 200}) == pytest.approx(0.05)
    assert entry_probability_of({"plans": 0, "evaluations": 200}) is None
    assert entry_probability_of({"plans": 3, "evaluations": 0}) is None
    assert entry_probability_of({}) is None


def test_the_baseline_runs_k_seeds_and_keeps_only_their_headline_numbers(book):
    real = runner.run_backtest(params_for(), settings_for(), book)
    seen: list[tuple[int, int, int, int]] = []
    saved: list[int] = []
    record = run_baseline(
        params_for(), settings_for(), book, real.summary, runs=3,
        progress=lambda *args: seen.append(args), on_seed_done=lambda rec: saved.append(len(rec["seeds"])),
    )
    assert record["status"] == "done" and record["requested_runs"] == 3 and [s["seed"] for s in record["seeds"]] == [1, 2, 3]
    assert record["entry_probability"] == pytest.approx(real.summary["plans"] / real.summary["evaluations"])
    assert saved == [1, 2, 3]
    assert {n for n, _, _, _ in seen} == {1, 2, 3} and all(total == 3 for _, total, _, _ in seen)
    for seed in record["seeds"]:
        assert set(seed) == {"seed", "total_return_pct", "sharpe", "average_r", "win_rate_pct", "trade_count", "max_drawdown_pct", "final_equity"}
    # the same call again is the same record: nothing is random between calls
    assert run_baseline(params_for(), settings_for(), book, real.summary, runs=3)["seeds"] == record["seeds"]


def test_cancelling_keeps_the_finished_seeds_and_drops_the_one_in_progress(book):
    real = runner.run_backtest(params_for(), settings_for(), book)
    finished = {"n": 0}
    record = run_baseline(
        params_for(), settings_for(), book, real.summary, runs=5,
        on_seed_done=lambda rec: finished.update(n=len(rec["seeds"])),
        should_cancel=lambda: finished["n"] >= 2,
    )
    assert record["status"] == "cancelled" and len(record["seeds"]) == 2 and record["requested_runs"] == 5

    # a cancel that arrives in the middle of a seed discards that seed
    days_seen = {"n": 0}
    record = run_baseline(
        params_for(), settings_for(), book, real.summary, runs=5,
        progress=lambda *a: days_seen.update(n=days_seen["n"] + 1), should_cancel=lambda: days_seen["n"] >= 5,
    )
    assert record["status"] == "cancelled" and record["seeds"] == []


def test_no_trade_plans_in_the_real_run_means_nothing_to_match(book):
    record = run_baseline(params_for(), settings_for(), book, {"plans": 0, "evaluations": 90}, runs=5)
    assert record["status"] == "skipped" and record["seeds"] == [] and "no trade plans" in record["note"]


# ------------------------------------------------------------------ where the real run sits


def test_percentile_and_chance_of_luck_on_known_values():
    seeds = [1.0, 2.0, 3.0, 4.0]
    mid = place_in_distribution(3.5, seeds)
    assert mid["percentile"] == pytest.approx(75.0) and mid["n"] == 4
    assert mid["chance_random_matches"] == pytest.approx(2 / 5)  # (1 + one random run at least as good) / (4 + 1)
    assert place_in_distribution(3.0, seeds)["percentile"] == pytest.approx(62.5)  # a tie counts half
    top = place_in_distribution(9.0, seeds)
    assert top["percentile"] == 100.0 and top["chance_random_matches"] == pytest.approx(1 / 5)  # never below 1/(K+1)
    bottom = place_in_distribution(-9.0, seeds)
    assert bottom["percentile"] == 0.0 and bottom["chance_random_matches"] == pytest.approx(1.0)
    assert mid["random_mean"] == pytest.approx(2.5) and mid["random_median"] == pytest.approx(2.5)
    assert mid["random_min"] == 1.0 and mid["random_max"] == 4.0


def test_missing_values_are_left_out_of_the_distribution():
    placed = place_in_distribution(1.0, [None, 0.0, float("nan"), 2.0])
    assert placed["n"] == 2 and placed["percentile"] == pytest.approx(50.0)
    assert place_in_distribution(None, [1.0, 2.0]) is None
    assert place_in_distribution(1.0, []) is None
    assert place_in_distribution(1.0, [None, None]) is None


def seed_row(i, total, r=0.0, sharpe=0.0):
    return {"seed": i, "total_return_pct": total, "average_r": r, "sharpe": sharpe, "trade_count": 5}


def test_reading_a_stored_baseline_places_the_real_run_and_states_the_small_k_caveat():
    stored = {"requested_runs": 5, "status": "done", "seeds": [seed_row(i + 1, float(i)) for i in range(5)]}
    out = read_baseline(stored, {"total_return_pct": 3.5, "average_r": None, "sharpe": None})
    assert out["available"] and out["placement"]["total_return_pct"]["percentile"] == pytest.approx(80.0)
    assert out["placement"]["average_r"] is None  # the real run has no average R: nothing to place
    assert out["enough_seeds"] is False and 5 < MIN_SEEDS_FOR_READING
    assert "Only 5 random runs" in out["caveat"] and "20 points" in out["caveat"] and "0.17" in out["caveat"]
    assert any("50/50" in note for note in out["notes"])


def test_no_baseline_is_reported_as_unavailable_not_as_zero():
    assert read_baseline(None, {"total_return_pct": 1.0})["available"] is False
    empty = read_baseline({"requested_runs": 5, "status": "skipped", "seeds": [], "note": "no plans"}, None)
    assert empty["available"] is False and empty["note"] == "no plans"
