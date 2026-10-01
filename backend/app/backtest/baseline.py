"""Can the strategy beat a random version of itself?

A backtest that makes money proves little: in a rising market almost any
long-biased rule does. The sharper question is whether the strategy's entries
carry information, so this module reruns the run K times with the entries chosen
at random and asks where the real run lands among them.

What is held the same as the real run
-------------------------------------
Same symbols, same days, same decision cadence, same settings (risk %, slippage,
commission, position and sector caps, time limit), the same stop and target
derivation (the very functions generate_trade_plan calls), the same sizing, the
same engine and the same exit rules. Every request is answered by the same as-of
data provider, so a random run cannot see the future either.

What is random
--------------
* Whether a symbol gets an entry on a day: a draw with probability p, the real
  run's own rate of trade plans per evaluated (symbol, day). So the random runs
  look at the same opportunities and try to enter as often as the real run did;
  how many of those attempts get filled is then up to the same position caps.
* The direction: long or short with equal odds, weighted by nothing. In a rising
  market the random shorts lose, which makes this baseline harder to be worse
  than; read a percentile with that in mind.

Each draw is a pure function of (seed, symbol, day), not of a shared stream, so a
seed always makes the same choices whatever else happened in the run.

Reading the result
------------------
K is small (20 by default, because each seed is a full run). The real run's
percentile among K seeds moves in steps of 100/K points, and the smallest "chance a
random run did at least this well" it can show is 1/(K+1). It is a sanity check
that the strategy is not just riding the market, not a significance test, and the
numbers say so.
"""

from __future__ import annotations

import hashlib
import logging
import math
from datetime import date
from typing import Any, Callable, Sequence

import numpy as np
from sqlmodel import Session

from app.analysis.indicators import latest_atr
from app.analysis.trend import analyze_chart
from app.backtest import runner
from app.backtest.data_provider import BacktestDataProvider, PriceBook
from app.backtest.metrics import compute_metrics, equity_rows, headline, trade_rows
from app.backtest.params import BacktestParams
from app.config import AppSettings
from app.portfolio.engine import (
    DuplicatePositionError,
    InsufficientCashError,
    MarketClosedError,
    MaxPositionsExceededError,
    SectorConcentrationError,
    StalePlanError,
)
from app.portfolio.models import TradePlanRecord
from app.risk.position_sizing import calculate_position_size, derive_targets
from app.schemas.trade_plan_schemas import TradePlanResponse
from app.services.trade_plan_service import (
    ATR_PERIOD,
    MAX_SCORE_FOR_CONFIDENCE,
    _derive_entry_and_stop,
)

logger = logging.getLogger(__name__)

DEFAULT_BASELINE_RUNS = 20
MAX_BASELINE_RUNS = 50
# Seeds are 1..K offset by this, so the numbers read as "seed 1, seed 2" yet a
# change of the constant reshuffles every draw.
SEED_SALT = 20261001
LONG_PROBABILITY = 0.5
# Below this many seeds no percentile is reported as a verdict input (the
# scorecard marks it "not enough data"); the numbers are still shown.
MIN_SEEDS_FOR_READING = 10
RANDOM_REASON = "Random-entry baseline: direction and day drawn at random, levels derived as the live plan does."
NOT_DRAWN_REASON = "Random-entry baseline: no entry drawn for this day."

# What stops an entry here is exactly what stops one in generate_trade_plan.
_OPEN_REFUSALS = (
    InsufficientCashError,
    DuplicatePositionError,
    MaxPositionsExceededError,
    SectorConcentrationError,
    StalePlanError,
)


def _uniform(seed: int, symbol: str, day: date, purpose: str) -> float:
    """A number in [0, 1) that depends only on its arguments."""
    digest = hashlib.blake2b(f"{seed}|{symbol}|{day.isoformat()}|{purpose}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


class RandomEntryPolicy:
    """The `entry_policy` the runner takes: random days and directions, live levels."""

    def __init__(self, seed: int, entry_probability: float, long_probability: float = LONG_PROBABILITY):
        if not 0.0 <= entry_probability <= 1.0:
            raise ValueError("entry_probability must be between 0 and 1")
        self.seed = seed
        self.entry_probability = entry_probability
        self.long_probability = long_probability

    def draw(self, symbol: str, day: date) -> str | None:
        """'long', 'short' or None (no entry), a pure function of seed, symbol and day."""
        if _uniform(self.seed, symbol, day, "enter") >= self.entry_probability:
            return None
        return "long" if _uniform(self.seed, symbol, day, "side") < self.long_probability else "short"

    def __call__(
        self,
        symbol: str,
        day: date,
        provider: BacktestDataProvider,
        session: Session,
        settings: AppSettings,
        *,
        allow_auto_execute: bool = True,
    ) -> TradePlanResponse:
        direction = self.draw(symbol, day)
        if direction is None:
            return TradePlanResponse(symbol=symbol, direction=None, reason=NOT_DRAWN_REASON, status="no_trade")

        ohlcv = provider.get_ohlcv(symbol, period="1y", interval="1d")
        chart = analyze_chart(ohlcv)
        atr = latest_atr(ohlcv, ATR_PERIOD)
        engine = runner._build_engine(session, provider, settings)
        # The same three calls, in the same order, as generate_trade_plan.
        entry, stop = _derive_entry_and_stop(direction, chart.price, chart.support, chart.resistance, atr)
        sizing = calculate_position_size(
            settings.paper_starting_cash, settings.default_risk_pct, entry, stop, engine.available_cash()
        )
        targets = derive_targets(entry, stop, direction, chart.support, chart.resistance)

        record = TradePlanRecord(
            symbol=symbol, direction=direction, entry=entry, stop=stop, tp1=targets.tp1, tp2=targets.tp2,
            rr1=targets.rr1, rr2=targets.rr2, suggested_shares=sizing.shares,
            account_risk_dollars=sizing.account_risk_dollars, confidence_score=0, confidence_points=0,
            confidence_points_max=MAX_SCORE_FOR_CONFIDENCE, signal_reasons=RANDOM_REASON, status="pending",
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        note = None
        if allow_auto_execute:
            try:
                engine.open_position(record)
                session.refresh(record)
            except MarketClosedError as exc:  # a trading day's 09:45 is open; kept for the same reason the live path has it
                note = str(exc)
            except _OPEN_REFUSALS as exc:
                note = f"Auto-execute skipped: {exc}"
        return TradePlanResponse(
            id=record.id, symbol=symbol, direction=direction, entry=entry, stop=stop, tp1=targets.tp1, tp2=targets.tp2,
            rr1=targets.rr1, rr2=targets.rr2, suggested_shares=sizing.shares,
            account_risk_dollars=sizing.account_risk_dollars, confidence_score=0, confidence_points=0,
            confidence_points_max=MAX_SCORE_FOR_CONFIDENCE, status=record.status, auto_execute_note=note,
            signal_reasons=RANDOM_REASON,
        )


# ---------------------------------------------------------------- reading the distribution


def place_in_distribution(real: float | None, seeds: Sequence[float | None]) -> dict[str, Any] | None:
    """Where `real` sits among the random runs' values of the same statistic.

    percentile: the share of random runs the real run beat, a tie counting half
    (0 = worse than all, 100 = better than all).
    chance_random_matches: (1 + random runs at least as good) / (K + 1), the usual
    one-sided permutation estimate of how often luck alone does this well. It can
    never be below 1/(K+1), however well the real run did.
    """
    values = [float(v) for v in seeds if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if real is None or not values:
        return None
    n = len(values)
    below = sum(1 for v in values if v < real)
    equal = sum(1 for v in values if v == real)
    at_least = sum(1 for v in values if v >= real)
    arr = np.array(values)
    return {
        "real": float(real),
        "n": n,
        "percentile": (below + 0.5 * equal) / n * 100,
        "chance_random_matches": (1 + at_least) / (n + 1),
        "random_mean": float(arr.mean()),
        "random_median": float(np.median(arr)),
        "random_p5": float(np.percentile(arr, 5)),
        "random_p95": float(np.percentile(arr, 95)),
        "random_min": float(arr.min()),
        "random_max": float(arr.max()),
    }


PLACED_STATISTICS = ("total_return_pct", "average_r", "sharpe")


def read_baseline(baseline: dict[str, Any] | None, real: dict[str, Any] | None) -> dict[str, Any]:
    """The stored baseline plus where the real run's headline numbers sit in it.
    `real` is metrics.headline() of the run itself."""
    if not baseline:
        return {"available": False, "reason": "this run did not include a random-entry baseline"}
    seeds = baseline.get("seeds", [])
    out: dict[str, Any] = {**baseline, "available": bool(seeds), "real": real, "placement": {}}
    k = len(seeds)
    if real is not None:
        for name in PLACED_STATISTICS:
            out["placement"][name] = place_in_distribution(real.get(name), [s.get(name) for s in seeds])
    out["enough_seeds"] = k >= MIN_SEEDS_FOR_READING
    out["caveat"] = (
        f"Only {k} random run{'s' if k != 1 else ''}: the real run's percentile moves in steps of "
        f"{100 / k:.0f} points and the smallest chance-of-luck figure it can show is {1 / (k + 1):.2f}. "
        "A sanity check that the strategy is not just riding the market, not a significance test."
        if k
        else "No random run has finished yet."
    )
    out["notes"] = [
        "Random direction is 50/50. In a rising market the random shorts lose, which makes the baseline easier to beat than a long-only one would be.",
        "Each random run takes the same number of trade plans per opportunity as the real run, then meets the same position caps.",
    ]
    return out


# ---------------------------------------------------------------- running the seeds


def entry_probability_of(summary: dict[str, Any]) -> float | None:
    """The real run's trade plans per evaluated (symbol, day), or None when it
    evaluated nothing or never made a plan (there is no rate to match)."""
    evaluations = summary.get("evaluations") or 0
    plans = summary.get("plans") or 0
    if evaluations <= 0 or plans <= 0:
        return None
    return min(1.0, plans / evaluations)


def seed_record(seed: int, result: runner.BacktestResult, starting_cash: float) -> dict[str, Any]:
    """The few numbers one random run keeps. Its trades and equity are discarded."""
    metrics = compute_metrics(equity_rows(result.equity), trade_rows(result.trades), starting_cash, include_series=False)
    return {"seed": seed, **headline(metrics), "final_equity": metrics["returns"]["final_equity"]}


def run_baseline(
    params: BacktestParams,
    settings: AppSettings,
    book: PriceBook,
    real_summary: dict[str, Any],
    *,
    runs: int = DEFAULT_BASELINE_RUNS,
    progress: Callable[[int, int, int, int], None] | None = None,
    on_seed_done: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run `runs` random-entry seeds and return the stored baseline record.

    `progress(seed_number, runs, days_done, days_total)` is called per simulated
    day of the seed in progress (seed_number counts from 1). `on_seed_done(record)`
    gets the partial record after every finished seed, so a caller can save it as
    it grows. A seed cut short by cancel is discarded; finished seeds are kept."""
    probability = entry_probability_of(real_summary)
    record: dict[str, Any] = {
        "requested_runs": runs,
        "entry_probability": probability,
        "real_plans": real_summary.get("plans"),
        "real_evaluations": real_summary.get("evaluations"),
        "long_probability": LONG_PROBABILITY,
        "seeds": [],
        "status": "done",
        "note": None,
    }
    if probability is None:
        record["status"] = "skipped"
        record["note"] = "The real run made no trade plans, so there is no entry rate to match and nothing to compare."
        return record
    starting_cash = settings.paper_starting_cash
    for number in range(1, runs + 1):
        if should_cancel is not None and should_cancel():
            record["status"] = "cancelled"
            break
        policy = RandomEntryPolicy(SEED_SALT + number, probability)
        result = runner.run_backtest(
            params, settings, book,
            progress=(lambda done, total, _day, n=number: progress(n, runs, done, total)) if progress else None,
            should_cancel=should_cancel,
            entry_policy=policy,
        )
        if result.status == "cancelled":
            record["status"] = "cancelled"
            break
        record["seeds"].append(seed_record(number, result, starting_cash))
        if on_seed_done is not None:
            on_seed_done(record)
    return record
