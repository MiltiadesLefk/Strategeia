"""Buy-and-hold references and the strategy-versus-SPY comparison, on synthetic series."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.backtest.benchmarks import build_benchmarks, regress
from app.backtest.metrics import EquityRow, compute_metrics

CASH = 100_000.0
DAYS = [date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6), date(2024, 3, 7), date(2024, 3, 8)]
PREVIOUS = date(2024, 3, 1)


def bars(days_and_closes: dict[date, float]) -> pd.DataFrame:
    days = list(days_and_closes)
    closes = list(days_and_closes.values())
    return pd.DataFrame({"date": pd.to_datetime(days), "open": closes, "high": closes, "low": closes, "close": closes, "volume": 1.0})


def loader_for(frames: dict[str, pd.DataFrame]):
    def load(symbol: str, start: date, end: date) -> pd.DataFrame:
        if symbol not in frames:
            raise KeyError(symbol)
        frame = frames[symbol]
        days = pd.to_datetime(frame["date"]).dt.date
        return frame[(days >= start) & (days <= end)].reset_index(drop=True)

    return load


# SPY: 400 the day before the run, then +1%, -2%, +1%, +1%, +2.01% (hand numbers below)
SPY = bars({PREVIOUS: 400.0, DAYS[0]: 404.0, DAYS[1]: 395.92, DAYS[2]: 399.8792, DAYS[3]: 403.878, DAYS[4]: 412.0})


def strategy_rows(values):
    return [EquityRow(d, v, 0) for d, v in zip(DAYS, values)]


# ------------------------------------------------------------------ regression


def test_an_exact_half_beta_with_a_constant_alpha():
    rng = np.random.default_rng(3)
    market = rng.normal(0.0004, 0.01, 60)
    strategy = 0.001 + 0.5 * market
    fit = regress(strategy, market)
    assert fit["n"] == 60
    assert fit["beta"] == pytest.approx(0.5)
    assert fit["alpha_annual_pct"] == pytest.approx(0.001 * 252 * 100)
    assert fit["r_squared"] == pytest.approx(1.0) and fit["correlation"] == pytest.approx(1.0)
    # a perfect fit has no residual to measure the error with
    assert fit["beta_t"] is None and fit["alpha_t"] is None


def test_beta_alpha_and_t_statistics_against_an_independent_least_squares():
    rng = np.random.default_rng(11)
    market = rng.normal(0.0005, 0.012, 250)
    strategy = 0.0004 + 0.8 * market + rng.normal(0, 0.004, 250)
    fit = regress(strategy, market)

    design = np.column_stack([np.ones_like(market), market])
    coefficients, residuals, *_ = np.linalg.lstsq(design, strategy, rcond=None)
    sigma2 = float(residuals[0]) / (250 - 2)
    covariance = sigma2 * np.linalg.inv(design.T @ design)
    assert fit["beta"] == pytest.approx(coefficients[1])
    assert fit["alpha_annual_pct"] == pytest.approx(coefficients[0] * 252 * 100)
    assert fit["beta_t"] == pytest.approx(coefficients[1] / np.sqrt(covariance[1, 1]))
    assert fit["alpha_t"] == pytest.approx(coefficients[0] / np.sqrt(covariance[0, 0]))
    assert fit["correlation"] == pytest.approx(np.corrcoef(strategy, market)[0, 1])
    assert fit["beta"] == pytest.approx(0.8, abs=0.1) and fit["n"] == 250


def test_a_regression_with_too_few_days_or_a_motionless_market_gives_nothing():
    assert regress(np.array([0.01, 0.02]), np.array([0.01, -0.01]))["beta"] is None
    assert regress(np.array([0.01, 0.02, 0.0, 0.01]), np.zeros(4))["beta"] is None


# ------------------------------------------------------------------ the SPY line and the comparison


def test_spy_buy_and_hold_over_exactly_the_runs_days_from_the_same_capital():
    strategy = strategy_rows([100_500, 101_000, 100_000, 102_000, 103_000])
    metrics = compute_metrics(strategy, [], CASH)
    out = build_benchmarks(strategy, CASH, metrics, [], loader_for({"SPY": SPY}))
    spy = out["spy"]
    assert out["available"] and spy["available"] and spy["days_aligned"] == 5 and spy["days_missing"] == 0
    # bought at the close before the first simulated day (400), so the first day's +1% counts
    assert "before" in spy["basis"]
    assert [p["day"] for p in spy["equity"]] == DAYS
    assert spy["equity"][0]["equity"] == pytest.approx(CASH * 404 / 400)
    assert spy["equity"][-1]["equity"] == pytest.approx(CASH * 412 / 400)
    assert spy["total_return_pct"] == pytest.approx(3.0)
    # the same statistics the strategy gets
    assert spy["max_drawdown_pct"] == pytest.approx((1 - 395.92 / 404) * 100)
    assert spy["sharpe"] is not None and spy["yearly_returns"][0]["year"] == 2024


def test_comparison_numbers_excess_outperformance_days_and_correlation():
    values = [100_500, 101_000, 100_000, 102_000, 103_000]
    strategy = strategy_rows(values)
    out = build_benchmarks(strategy, CASH, compute_metrics(strategy, [], CASH), [], loader_for({"SPY": SPY}))
    c = out["comparison"]
    assert c["strategy_return_pct"] == pytest.approx(3.0) and c["spy_return_pct"] == pytest.approx(3.0)
    assert c["excess_return_pct"] == pytest.approx(0.0, abs=1e-9)
    # daily returns, strategy vs SPY: +0.5/+1.0, +0.4975/-2.0, -0.99/+1.0, +2.0/+1.0, +0.98/+2.01 (%)
    # -> the strategy was ahead on days 2 and 4 only
    assert c["days_compared"] == 5 and c["outperform_days_pct"] == pytest.approx(40.0)
    spy_returns = np.array([404 / 400, 395.92 / 404, 399.8792 / 395.92, 403.878 / 399.8792, 412 / 403.878]) - 1
    strategy_returns = np.array([100_500 / CASH, 101_000 / 100_500, 100_000 / 101_000, 102_000 / 100_000, 103_000 / 102_000]) - 1
    assert c["correlation"] == pytest.approx(np.corrcoef(strategy_returns, spy_returns)[0, 1])
    assert c["beta"] == pytest.approx(np.cov(strategy_returns, spy_returns)[0, 1] / np.var(spy_returns, ddof=1))
    assert c["n"] == 5


def test_a_strategy_that_is_a_scaled_copy_of_spy_has_beta_equal_to_the_scale():
    # strategy return = 0.5 x SPY return every day
    spy_closes = [404.0, 395.92, 399.8792, 403.878, 412.0]
    previous, level = 400.0, CASH
    values = []
    for close in spy_closes:
        level *= 1 + 0.5 * (close / previous - 1)
        values.append(level)
        previous = close
    strategy = strategy_rows(values)
    out = build_benchmarks(strategy, CASH, compute_metrics(strategy, [], CASH), [], loader_for({"SPY": SPY}))
    assert out["comparison"]["beta"] == pytest.approx(0.5)
    assert out["comparison"]["correlation"] == pytest.approx(1.0)
    assert out["comparison"]["alpha_annual_pct"] == pytest.approx(0.0, abs=1e-6)


def test_the_first_days_open_is_the_basis_when_no_earlier_bar_is_stored():
    only_run_days = bars({d: c for d, c in zip(DAYS, [404.0, 395.92, 399.8792, 403.878, 412.0])})
    strategy = strategy_rows([100_000] * 5)
    out = build_benchmarks(strategy, CASH, compute_metrics(strategy, [], CASH), [], loader_for({"SPY": only_run_days}))
    assert "open" in out["spy"]["basis"]
    assert out["spy"]["equity"][0]["equity"] == pytest.approx(CASH)  # bought at the first day's open, which the fixture sets = close


def test_missing_spy_history_is_reported_not_invented():
    strategy = strategy_rows([100_000] * 5)
    out = build_benchmarks(strategy, CASH, compute_metrics(strategy, [], CASH), [], loader_for({}))
    assert out["available"] is False and out["comparison"] is None
    assert "SPY" in out["spy"]["reason"]


def test_a_day_without_a_spy_bar_is_left_out_of_both_sides_of_the_comparison():
    holey = SPY[SPY["date"] != pd.Timestamp(DAYS[2])]
    strategy = strategy_rows([100_500, 101_000, 100_000, 102_000, 103_000])
    out = build_benchmarks(strategy, CASH, compute_metrics(strategy, [], CASH), [], loader_for({"SPY": holey}))
    assert out["spy"]["days_aligned"] == 4 and out["spy"]["days_missing"] == 1
    assert out["comparison"]["days_compared"] == 4
    assert DAYS[2] not in [p["day"] for p in out["spy"]["equity"]]


# ------------------------------------------------------------------ equal-weight buy-and-hold


def test_equal_weight_basket_is_equal_dollars_held_and_labelled_survivor_biased():
    aaa = bars({PREVIOUS: 10.0, DAYS[0]: 11.0, DAYS[1]: 12.0, DAYS[2]: 12.0, DAYS[3]: 13.0, DAYS[4]: 15.0})
    bbb = bars({PREVIOUS: 50.0, DAYS[0]: 50.0, DAYS[1]: 45.0, DAYS[2]: 40.0, DAYS[3]: 40.0, DAYS[4]: 50.0})
    strategy = strategy_rows([100_000] * 5)
    out = build_benchmarks(strategy, CASH, compute_metrics(strategy, [], CASH), ["AAA", "BBB", "NEW"], loader_for({"SPY": SPY, "AAA": aaa, "BBB": bbb}))
    basket = out["equal_weight"]
    assert basket["available"] and basket["survivor_biased"] is True and "Survivor" in basket["label"]
    assert basket["symbols_used"] == ["AAA", "BBB"]
    assert [e["symbol"] for e in basket["symbols_excluded"]] == ["NEW"]
    # half the money in each: value = 100000 x mean(price / base price)
    expected = [CASH * (a / 10 + b / 50) / 2 for a, b in zip([11, 12, 12, 13, 15], [50, 45, 40, 40, 50])]
    assert [p["equity"] for p in basket["equity"]] == pytest.approx(expected)
    assert basket["total_return_pct"] == pytest.approx((expected[-1] / CASH - 1) * 100)
    assert basket["max_drawdown_pct"] > 0


def test_a_symbol_missing_a_day_keeps_its_last_known_price():
    aaa = bars({PREVIOUS: 10.0, DAYS[0]: 11.0, DAYS[1]: 12.0, DAYS[3]: 13.0, DAYS[4]: 15.0})  # no bar on DAYS[2]
    strategy = strategy_rows([100_000] * 5)
    out = build_benchmarks(strategy, CASH, compute_metrics(strategy, [], CASH), ["AAA"], loader_for({"SPY": SPY, "AAA": aaa}))
    values = [p["equity"] for p in out["equal_weight"]["equity"]]
    assert values[2] == pytest.approx(CASH * 12 / 10)  # unchanged from the day before: held, not traded


def test_equal_weight_with_no_usable_symbol_is_unavailable():
    strategy = strategy_rows([100_000] * 5)
    out = build_benchmarks(strategy, CASH, compute_metrics(strategy, [], CASH), ["ZZZ"], loader_for({"SPY": SPY}))
    assert out["equal_weight"]["available"] is False and out["spy"]["available"] is True


def test_a_run_with_no_days_has_no_benchmarks():
    out = build_benchmarks([], CASH, compute_metrics([], [], CASH), ["AAA"], loader_for({"SPY": SPY}))
    assert out["available"] is False
