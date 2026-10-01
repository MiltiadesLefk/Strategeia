"""What the same money would have done without the strategy.

Two reference lines for a finished run, both over exactly the run's simulated days
and from the same starting capital, both without trading costs (holding is free):

* **SPY buy-and-hold**: the fair "why not just buy the index" yardstick. Read from
  the local price-history store, never from the network.
* **Equal-weight buy-and-hold of the run's own symbols**: equal dollars in each
  symbol on the first day, then held. It is **survivor-biased** on purpose and
  labelled so: the symbols are today's index members, so it never held a company
  that was later dropped, which flatters it (and the strategy, which only ever
  traded these same names).

The comparison block puts the strategy against SPY: excess return, beta and alpha
from a regression of the strategy's daily returns on SPY's (with the number of
days and t-statistics, because a regression on a few dozen days says little),
correlation, and how often the strategy beat SPY on a day. A strategy that is in
cash most days will often "lose" a day to SPY simply by not being invested; that
number is shown as a fact, not a score.

Everything is computed from stored rows and stored bars on every read.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from app.backtest.metrics import (
    TRADING_DAYS_PER_YEAR,
    EquityRow,
    compute_metrics,
)

BENCHMARK_SYMBOL = "SPY"
# How far before the first simulated day bars are read, to find the close the
# buy-and-hold references are bought at (the last close before the run began).
BASE_LOOKBACK_DAYS = 14
# A regression needs at least this many aligned days to have any residual degrees of freedom.
MIN_DAYS_FOR_REGRESSION = 3
MIN_VARIANCE = 1e-18

EQUAL_WEIGHT_LABEL = (
    "Survivor-biased: equal dollars in today's index members, held from the first day. "
    "It never owned a company that was later dropped, so it is flattered."
)

# symbol, start, end (both inclusive) -> frame with columns date, open, close (disk only)
BarsLoader = Callable[[str, date, date], pd.DataFrame]


def store_loader(store) -> BarsLoader:
    """A BarsLoader over the local history store, reading the disk only."""

    def load(symbol: str, start: date, end: date) -> pd.DataFrame:
        return store.get_daily_history(symbol, start=start, end=end, refresh="never")

    return load


# ---------------------------------------------------------------- regression


def regress(strategy: np.ndarray, market: np.ndarray) -> dict[str, Any]:
    """Ordinary least squares of the strategy's daily returns on the market's:
    strategy = alpha + beta x market + noise. Alpha is reported per year
    (daily alpha x 252, simple not compounded). t-statistics are None when the
    fit is exact (no residual to measure error with) or has too few days."""
    n = len(strategy)
    base: dict[str, Any] = {
        "n": n, "beta": None, "beta_t": None, "alpha_annual_pct": None, "alpha_t": None,
        "r_squared": None, "correlation": None,
    }
    if n < MIN_DAYS_FOR_REGRESSION:
        return base
    x_mean, y_mean = float(market.mean()), float(strategy.mean())
    sxx = float(((market - x_mean) ** 2).sum())
    syy = float(((strategy - y_mean) ** 2).sum())
    if sxx < MIN_VARIANCE:
        return base  # the market never moved: beta is undefined
    sxy = float(((market - x_mean) * (strategy - y_mean)).sum())
    beta = sxy / sxx
    alpha = y_mean - beta * x_mean
    residual = strategy - alpha - beta * market
    sse = float((residual**2).sum())
    s2 = sse / (n - 2)
    se_beta = math.sqrt(s2 / sxx)
    se_alpha = math.sqrt(s2 * (1.0 / n + x_mean**2 / sxx))
    base.update(
        beta=beta,
        beta_t=beta / se_beta if se_beta > 1e-15 else None,
        alpha_annual_pct=alpha * TRADING_DAYS_PER_YEAR * 100,
        alpha_t=alpha / se_alpha if se_alpha > 1e-15 else None,
        r_squared=1 - sse / syy if syy >= MIN_VARIANCE else None,
        correlation=sxy / math.sqrt(sxx * syy) if syy >= MIN_VARIANCE else None,
    )
    return base


# ---------------------------------------------------------------- series


def _closes_by_day(frame: pd.DataFrame | None) -> dict[date, float]:
    if frame is None or frame.empty:
        return {}
    return {pd.Timestamp(d).date(): float(c) for d, c in zip(frame["date"], frame["close"])}


def _base_close(frame: pd.DataFrame | None, first_day: date) -> tuple[float | None, str | None]:
    """What a buy-and-hold bought on the first day pays: the last close before it
    (so the first day's move counts, as it does for the strategy's first return),
    or the first day's open when no earlier bar is stored."""
    if frame is None or frame.empty:
        return None, None
    days = pd.to_datetime(frame["date"]).dt.date
    before = frame[days < first_day]
    if not before.empty:
        return float(before["close"].iloc[-1]), "the close before the first simulated day"
    on_day = frame[days == first_day]
    if not on_day.empty and float(on_day["open"].iloc[0]) > 0:
        return float(on_day["open"].iloc[0]), "the open of the first simulated day (no earlier bar is stored)"
    return None, None


def _equity_rows(days: Sequence[date], values: Sequence[float]) -> list[EquityRow]:
    return [EquityRow(d, float(v), 0) for d, v in zip(days, values)]


def _returns(values: Sequence[float], base: float) -> np.ndarray:
    path = np.array([base, *values], dtype=float)
    return path[1:] / path[:-1] - 1.0


def _reference_block(rows: list[EquityRow], starting_cash: float) -> dict[str, Any]:
    """The reference line: its series and the same statistics as the strategy's."""
    metrics = compute_metrics(rows, [], starting_cash, include_series=True)
    return {
        "equity": [{"day": r.day, "equity": r.equity} for r in rows],
        "total_return_pct": metrics["returns"]["total_return_pct"],
        "cagr_pct": metrics["returns"]["cagr_pct"],
        "volatility_pct": metrics["returns"]["volatility_pct"],
        "sharpe": metrics["returns"]["sharpe"],
        "sortino": metrics["returns"]["sortino"],
        "calmar": metrics["returns"]["calmar"],
        "max_drawdown_pct": metrics["drawdown"]["max_drawdown_pct"],
        "yearly_returns": metrics["yearly_returns"],
        "drawdown_series": metrics["drawdown_series"],
    }


def _spy(equity: Sequence[EquityRow], starting_cash: float, loader: BarsLoader, first: date, last: date) -> dict[str, Any]:
    try:
        frame = loader(BENCHMARK_SYMBOL, first - timedelta(days=BASE_LOOKBACK_DAYS), last)
    except Exception as exc:  # noqa: BLE001 - a missing store must not break the rest of the page
        return {"available": False, "reason": f"{BENCHMARK_SYMBOL} history could not be read: {exc}"}
    base, basis = _base_close(frame, first)
    closes = _closes_by_day(frame)
    if base is None or not closes:
        return {"available": False, "reason": f"{BENCHMARK_SYMBOL} history is not stored for these days"}
    aligned = [row for row in equity if row.day in closes]
    if not aligned:
        return {"available": False, "reason": f"{BENCHMARK_SYMBOL} has no bar on any simulated day"}
    values = [starting_cash * closes[row.day] / base for row in aligned]
    rows = _equity_rows([r.day for r in aligned], values)
    return {
        "available": True,
        "symbol": BENCHMARK_SYMBOL,
        "basis": basis,
        "days_aligned": len(aligned),
        "days_missing": len(equity) - len(aligned),
        **_reference_block(rows, starting_cash),
        "_aligned_strategy": aligned,
        "_base_equity": starting_cash,
    }


def _comparison(strategy_metrics: dict[str, Any], spy: dict[str, Any], starting_cash: float) -> dict[str, Any]:
    aligned: list[EquityRow] = spy["_aligned_strategy"]
    strategy_values = [r.equity for r in aligned]
    spy_values = [p["equity"] for p in spy["equity"]]
    rs = _returns(strategy_values, starting_cash)
    rm = _returns(spy_values, starting_cash)
    fit = regress(rs, rm)
    strategy_returns = strategy_metrics["returns"]
    # Over the aligned days only, so the two totals compare like with like when SPY lacks a bar.
    strategy_total = (strategy_values[-1] / starting_cash - 1) * 100
    spy_total = spy["total_return_pct"]
    strategy_sharpe = strategy_returns["sharpe"]
    return {
        "strategy_return_pct": strategy_total,
        "spy_return_pct": spy_total,
        "excess_return_pct": strategy_total - spy_total if spy_total is not None else None,
        "strategy_sharpe": strategy_sharpe,
        "spy_sharpe": spy["sharpe"],
        "sharpe_difference": strategy_sharpe - spy["sharpe"] if strategy_sharpe is not None and spy["sharpe"] is not None else None,
        "strategy_max_drawdown_pct": strategy_metrics["drawdown"]["max_drawdown_pct"],
        "spy_max_drawdown_pct": spy["max_drawdown_pct"],
        "outperform_days_pct": float((rs > rm).mean() * 100) if len(rs) else None,
        "days_compared": len(rs),
        **fit,
    }


def _equal_weight(
    run_symbols: Sequence[str], days: Sequence[date], starting_cash: float, loader: BarsLoader, first: date, last: date
) -> dict[str, Any]:
    used: list[str] = []
    excluded: list[dict[str, str]] = []
    ratios: list[np.ndarray] = []
    for symbol in run_symbols:
        try:
            frame = loader(symbol, first - timedelta(days=BASE_LOOKBACK_DAYS), last)
        except Exception as exc:  # noqa: BLE001
            excluded.append({"symbol": symbol, "reason": f"history could not be read: {exc}"})
            continue
        base, _ = _base_close(frame, first)
        closes = _closes_by_day(frame)
        if base is None or not closes:
            excluded.append({"symbol": symbol, "reason": "no stored bar at the start of the run"})
            continue
        # A day without a bar keeps the last known close: the holding is unchanged, nothing is traded.
        series, carry = [], base
        for day in days:
            carry = closes.get(day, carry)
            series.append(carry / base)
        ratios.append(np.array(series))
        used.append(symbol)
    base_result: dict[str, Any] = {"label": EQUAL_WEIGHT_LABEL, "survivor_biased": True, "symbols_used": used, "symbols_excluded": excluded}
    if not used:
        return {**base_result, "available": False, "reason": "none of the run's symbols has stored history at the start of the run"}
    values = starting_cash * np.mean(np.vstack(ratios), axis=0)
    rows = _equity_rows(days, values)
    return {**base_result, "available": True, **_reference_block(rows, starting_cash)}


def build_benchmarks(
    equity: Sequence[EquityRow],
    starting_cash: float,
    strategy_metrics: dict[str, Any],
    run_symbols: Sequence[str],
    loader: BarsLoader,
) -> dict[str, Any]:
    """The SPY line, the equal-weight line and the comparison, for one run."""
    if not equity:
        return {"available": False, "reason": "the run has no simulated days"}
    first, last = equity[0].day, equity[-1].day
    days = [row.day for row in equity]
    spy = _spy(equity, starting_cash, loader, first, last)
    comparison = None
    if spy["available"]:
        comparison = _comparison(strategy_metrics, spy, starting_cash)
        spy.pop("_aligned_strategy")
        spy.pop("_base_equity")
    return {
        "available": bool(spy["available"]),
        "starting_capital": starting_cash,
        "costs": "none: both references are bought on the first day and held, with no commission or slippage",
        "spy": spy,
        "comparison": comparison,
        "equal_weight": _equal_weight(run_symbols, days, starting_cash, loader, first, last),
    }
