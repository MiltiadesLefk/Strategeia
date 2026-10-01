"""Walk-forward validation: does the strategy still work on days it was not tuned on?

The strategy itself has no fitted parameters, but its settings (the confidence bar,
the risk per trade, the holding limit, slippage) are things a person tunes by looking
at backtests, and tuning on the same days you then judge on flatters any result. This
module tests that honestly:

1. The period is cut into K consecutive **folds**. Each has a **train** window and a
   **test** window after it, separated by an **embargo** gap of trading days. Test
   windows are consecutive and never overlap each other or any train window of their
   own fold. A train window is rolling (a fixed length that slides forward) or
   anchored (it always starts at the beginning of the period).
2. For each fold, every settings variant of a small grid is run on the **train**
   window; the one with the best in-sample Sharpe ratio is **selected**, and only that
   one is then run on the **test** window ("out of sample"). Without a grid there is a
   single variant (the settings as they are) and nothing is selected: train and test
   are simply two looks at the same rules.
3. The test results are stitched into one out-of-sample equity curve and one list of
   trades. Its Sharpe ratio goes through the deflated Sharpe ratio (deflated_sharpe.py)
   with the number of variants tried, so trying many settings is penalised.

Every window is a separate run of the same runner the Backtest Lab uses, on its own
throwaway database, starting with the full starting cash and no open positions. The
price history is loaded once for the whole period; the data provider still answers
each simulated day only from what was known then, so a window never sees past its
own days. Because windows are independent runs, the embargo is a conservative extra
gap rather than something the maths needs: no position or fitted value carries over
from a train window into a test window.

Pure computation: nothing here touches the database (service.py stores the result).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import numpy as np
from pydantic import field_validator, model_validator

from app.backtest.calendar import trading_days
from app.backtest.coverage import describe_coverage
from app.backtest.data_provider import PriceBook
from app.backtest.deflated_sharpe import (
    DEFAULT_CONFIDENCE,
    TRADING_DAYS_PER_YEAR,
    annualise,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_track_record_length,
    probabilistic_sharpe_ratio,
    sharpe_moments,
    trial_sharpe_variance,
)
from app.backtest.metrics import EquityRow, compute_metrics, daily_returns, equity_rows, headline, trade_rows
from app.backtest.params import BacktestInputError, BacktestParams, SettingsOverrides, effective_settings
from app.backtest.runner import BacktestResult, run_backtest
from app.backtest.scorecard import FAIL, INSUFFICIENT, PASS, _check
from app.config import AppSettings

# ---------------------------------------------------------------- limits

MIN_FOLDS = 2
MAX_FOLDS = 12
DEFAULT_FOLDS = 4
# The train window is this many times as long as one test window.
DEFAULT_TRAIN_RATIO = 2.0
MIN_TRAIN_RATIO, MAX_TRAIN_RATIO = 0.5, 8.0
# Trading days left empty between the end of a train window and the start of its test window.
DEFAULT_EMBARGO_DAYS = 5
MAX_EMBARGO_DAYS = 60
# Shorter windows than this say almost nothing (a month of trading days is the floor).
MIN_WINDOW_DAYS = 20
MAX_VALUES_PER_KNOB = 5
MAX_VARIANTS = 16
# Every variant costs a full run on every train window, so the total is capped to keep a job
# from occupying the one worker for hours.
MAX_TOTAL_RUNS = 100
# "Most folds" needs at least this many folds to mean something.
MIN_FOLDS_FOR_READING = 3
# The Sharpe measured on this few out-of-sample days is too noisy to put a probability on.
MIN_OOS_DAYS_FOR_DSR = 60

MODE_ROLLING = "rolling"
MODE_ANCHORED = "anchored"


@dataclass(frozen=True)
class Knob:
    """A setting the parameter sweep may vary."""

    name: str
    label: str
    kind: str  # "int" | "float"
    low: float
    high: float
    help: str


# Only settings that change what the strategy does with a signal, not what the signal is,
# and that a person would genuinely tune. Anything else stays fixed by the run itself.
KNOBS: dict[str, Knob] = {
    k.name: k
    for k in (
        Knob("min_confidence_for_trade", "Confidence bar (%)", "int", 0, 100, "Plans scoring below this are not traded."),
        Knob("default_risk_pct", "Risk per trade (%)", "float", 0.1, 5.0, "Share of the account risked between entry and stop."),
        Knob("max_holding_days", "Holding limit (trading days)", "int", 0, 60, "A position is closed after this many days; 0 means no limit."),
        Knob("slippage_bps", "Slippage (basis points)", "float", 0, 100, "Cost added to market fills; higher is more pessimistic."),
    )
}


# ---------------------------------------------------------------- the request


class ValidationParams(BacktestParams):
    """A walk-forward validation request. symbols/start/end/decision_every_n_days/overrides
    are the same as a backtest's; `overrides` are the settings held fixed for every variant
    and a grid knob replaces its override."""

    folds: int = DEFAULT_FOLDS
    mode: Literal["rolling", "anchored"] = MODE_ANCHORED
    train_ratio: float = DEFAULT_TRAIN_RATIO
    embargo_days: int = DEFAULT_EMBARGO_DAYS
    # knob name -> the values to try. Empty: one variant, the settings as they are.
    grid: dict[str, list[float]] = {}

    @field_validator("folds")
    @classmethod
    def _folds(cls, value: int) -> int:
        if not MIN_FOLDS <= value <= MAX_FOLDS:
            raise ValueError(f"folds must be between {MIN_FOLDS} and {MAX_FOLDS}")
        return value

    @field_validator("train_ratio")
    @classmethod
    def _train_ratio(cls, value: float) -> float:
        if not MIN_TRAIN_RATIO <= value <= MAX_TRAIN_RATIO:
            raise ValueError(f"train_ratio must be between {MIN_TRAIN_RATIO} and {MAX_TRAIN_RATIO}")
        return value

    @field_validator("embargo_days")
    @classmethod
    def _embargo(cls, value: int) -> int:
        if not 0 <= value <= MAX_EMBARGO_DAYS:
            raise ValueError(f"embargo_days must be between 0 and {MAX_EMBARGO_DAYS}")
        return value

    @field_validator("grid")
    @classmethod
    def _grid(cls, value: dict[str, list[float]]) -> dict[str, list[float]]:
        cleaned: dict[str, list[float]] = {}
        for name, values in value.items():
            knob = KNOBS.get(name)
            if knob is None:
                raise ValueError(f"'{name}' cannot be swept; allowed: {', '.join(KNOBS)}")
            if not values:
                continue
            if len(values) > MAX_VALUES_PER_KNOB:
                raise ValueError(f"at most {MAX_VALUES_PER_KNOB} values per setting ({name})")
            unique: list[float] = []
            for raw in values:
                if not math.isfinite(raw) or not knob.low <= raw <= knob.high:
                    raise ValueError(f"{name} must be between {knob.low:g} and {knob.high:g}")
                if knob.kind == "int":
                    if raw != int(raw):
                        raise ValueError(f"{name} takes whole numbers")
                    raw = int(raw)
                if raw not in unique:
                    unique.append(raw)
            cleaned[name] = unique
        return cleaned

    @model_validator(mode="after")
    def _size(self) -> "ValidationParams":
        variants = count_variants(self.grid)
        if variants > MAX_VARIANTS:
            raise ValueError(f"the grid makes {variants} variants; at most {MAX_VARIANTS}")
        total = total_runs(variants, self.folds)
        if total > MAX_TOTAL_RUNS:
            raise ValueError(f"{variants} variants x {self.folds} folds is {total} runs; at most {MAX_TOTAL_RUNS}")
        return self


def count_variants(grid: dict[str, list[float]]) -> int:
    return math.prod(len(v) for v in grid.values() if v) if any(grid.values()) else 1


def total_runs(variants: int, folds: int) -> int:
    """Train runs (every variant, every fold) plus one test run per fold."""
    return variants * folds + folds


def count_trials(variants: int, folds: int) -> int:
    """How many things were tried for the deflated Sharpe ratio. With one variant nothing
    was searched or selected (every fold runs the same rules), so it is a single trial;
    with a grid every variant was tried in every fold."""
    return 1 if variants <= 1 else variants * folds


def make_variants(grid: dict[str, list[float]]) -> list[dict[str, float]]:
    """Every combination of the grid, in a fixed order (knobs sorted by name, values as given)."""
    names = sorted(n for n, v in grid.items() if v)
    if not names:
        return [{}]
    return [dict(zip(names, combo)) for combo in itertools.product(*(grid[n] for n in names))]


# ---------------------------------------------------------------- folds


@dataclass(frozen=True)
class Window:
    start: date
    end: date
    days: int


@dataclass(frozen=True)
class Fold:
    index: int  # 1-based
    train: Window
    test: Window
    embargo_days: int


def make_folds(days: Sequence[date], folds: int, mode: str, train_ratio: float, embargo_days: int) -> list[Fold]:
    """Cut `days` (consecutive trading days) into `folds` train/test pairs.

    One test window is T days, the train window is round(train_ratio * T) days, and the
    layout (train, embargo, K test windows back to back) is placed so that it ends on the
    last day; a remainder, if any, is dropped from the *start* so the newest data is used.
    Fold i's test window is the i-th of the K consecutive ones; its train window ends
    `embargo_days` trading days before it starts (rolling: the same length every time;
    anchored: from the first day laid out)."""
    n = len(days)
    t = int((n - embargo_days) / (train_ratio + folds))
    train_len = int(round(train_ratio * t))
    if t < MIN_WINDOW_DAYS or train_len < MIN_WINDOW_DAYS:
        raise BacktestInputError(
            f"{n} trading days is too short for {folds} folds: each test window would be {t} days and each "
            f"train window {train_len}, and both need at least {MIN_WINDOW_DAYS}. Use a longer period or fewer folds."
        )
    used = train_len + embargo_days + folds * t
    offset = n - used
    out: list[Fold] = []
    for i in range(folds):
        test_start = offset + train_len + embargo_days + i * t
        train_end = test_start - embargo_days - 1
        train_start = offset if mode == MODE_ANCHORED else train_end - train_len + 1
        out.append(
            Fold(
                index=i + 1,
                train=Window(days[train_start], days[train_end], train_end - train_start + 1),
                test=Window(days[test_start], days[test_start + t - 1], t),
                embargo_days=embargo_days,
            )
        )
    return out


# ---------------------------------------------------------------- one window


RunWindow = Callable[..., BacktestResult]


class ValidationCancelled(Exception):
    """Raised inside a validation when the cancel flag is seen; carries the folds finished so far."""


def _window_params(params: ValidationParams, window: Window, overrides: dict[str, Any]) -> BacktestParams:
    return BacktestParams(
        symbols=params.symbols, start=window.start, end=window.end,
        decision_every_n_days=params.decision_every_n_days, overrides=SettingsOverrides(**overrides),
    )


def _stats(result: BacktestResult, starting_cash: float) -> tuple[dict[str, Any], list[EquityRow]]:
    """The few numbers of one window, and its equity rows."""
    equity = equity_rows(result.equity)
    metrics = compute_metrics(equity, trade_rows(result.trades), starting_cash, include_series=False)
    out = headline(metrics)
    out["trading_days"] = len(equity)
    out["open_at_end"] = metrics["trades"]["open_at_end"]
    return out, equity


def select_variant(trials: Sequence[dict[str, Any]]) -> tuple[int, str]:
    """The index of the variant with the best in-sample Sharpe (a tie goes to the lower index,
    which is deterministic), and the basis. A variant without a defined Sharpe (no trades, flat
    equity) never wins; if none has one, the first variant is used and the basis says so."""
    best, best_key = 0, None
    for index, trial in enumerate(trials):
        sharpe = trial["is"]["sharpe"]
        if sharpe is None:
            continue
        key = (sharpe, -index)
        if best_key is None or key > best_key:
            best, best_key = index, key
    return best, ("in-sample Sharpe" if best_key is not None else "first variant (no variant had a defined Sharpe)")


def stitch_equity(windows: Sequence[tuple[float, Sequence[EquityRow]]]) -> list[dict[str, Any]]:
    """Chain window equity curves into one: each window's curve is rescaled by its own
    start so a window continues from where the previous one ended. The first starts at its
    own starting cash."""
    out: list[dict[str, Any]] = []
    carried = windows[0][0] if windows else 0.0
    for starting_cash, rows in windows:
        if not rows or starting_cash <= 0:
            continue
        factor = carried / starting_cash
        for row in rows:
            out.append({"day": row.day, "equity": row.equity * factor, "open_positions": row.open_positions})
        carried = rows[-1].equity * factor
    return out


# ---------------------------------------------------------------- the whole validation


ProgressFn = Callable[[int, int, str, int, int], None]
FoldDoneFn = Callable[[dict[str, Any]], None]


def run_validation(
    params: ValidationParams,
    base_settings: AppSettings,
    book: PriceBook,
    *,
    run_window: RunWindow = run_backtest,
    progress: ProgressFn | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_fold_done: FoldDoneFn | None = None,
) -> dict[str, Any]:
    """Run the walk-forward validation and return the result record (JSON-safe).

    `run_window(params, settings, book, progress=, should_cancel=)` is the runner (a
    test passes a fake). `progress(runs_done, runs_total, label, days_done, days_total)`
    is called while a window runs. `on_fold_done(partial_record)` gets the record after
    every finished fold. On cancel the record has status "cancelled", the folds finished
    so far, and no aggregate or deflated Sharpe (those would be computed from a
    selection that was cut short)."""
    params.validate_dates()
    days = trading_days(params.start, params.end)
    folds = make_folds(days, params.folds, params.mode, params.train_ratio, params.embargo_days)
    variants = make_variants(params.grid)
    n_trials = count_trials(len(variants), len(folds))
    runs_total = total_runs(len(variants), len(folds))
    fixed = params.overrides.model_dump(exclude_none=True)

    def overrides_for(variant: dict[str, float]) -> dict[str, Any]:
        return {**fixed, **variant}

    base_effective = effective_settings(base_settings, params.overrides)
    record: dict[str, Any] = {
        "status": "running",
        "mode": params.mode,
        "folds_requested": len(folds),
        "train_ratio": params.train_ratio,
        "embargo_days": params.embargo_days,
        "selection_basis": "in-sample Sharpe" if len(variants) > 1 else "none (a single variant)",
        "variants": [{"index": i, "params": v} for i, v in enumerate(variants)],
        "n_variants": len(variants),
        "n_trials": n_trials,
        "runs_total": runs_total,
        "period": {"first_day": days[0].isoformat(), "last_day": days[-1].isoformat(), "trading_days": len(days)},
        "coverage": describe_coverage(base_effective.min_confidence_for_trade, base_settings.min_confidence_for_trade),
        "folds": [],
        "aggregate": None,
        "dsr": None,
        "oos_equity": [],
    }
    state = {"done": 0}

    def run(window: Window, overrides: dict[str, Any], label: str):
        if should_cancel is not None and should_cancel():
            raise ValidationCancelled()
        settings = effective_settings(base_settings, SettingsOverrides(**overrides))
        wp = _window_params(params, window, overrides)

        def day_progress(done: int, total: int, _day: date) -> None:
            if progress is not None:
                progress(state["done"], runs_total, label, done, total)

        result = run_window(wp, settings, book, progress=day_progress, should_cancel=should_cancel)
        if result.status == "cancelled":
            raise ValidationCancelled()
        state["done"] += 1
        if progress is not None:
            progress(state["done"], runs_total, label, 0, 0)
        return result, settings.paper_starting_cash

    oos_windows: list[tuple[float, list[EquityRow]]] = []
    oos_trades: list[dict[str, Any]] = []
    oos_returns: list[np.ndarray] = []
    all_is_sharpes: list[float | None] = []
    try:
        for fold in folds:
            trials: list[dict[str, Any]] = []
            for index, variant in enumerate(variants):
                label = f"fold {fold.index}/{len(folds)}: train, variant {index + 1}/{len(variants)}"
                result, cash = run(fold.train, overrides_for(variant), label)
                stats, _ = _stats(result, cash)
                trials.append({"variant_index": index, "params": variant, "is": stats})
                all_is_sharpes.append(_native_sharpe(result, cash))
            chosen, basis = select_variant(trials) if len(variants) > 1 else (0, "none (a single variant)")
            label = f"fold {fold.index}/{len(folds)}: test, selected variant {chosen + 1}"
            result, cash = run(fold.test, overrides_for(variants[chosen]), label)
            oos_stats, oos_equity = _stats(result, cash)
            oos_windows.append((cash, oos_equity))
            oos_trades.extend(result.trades)
            oos_returns.append(daily_returns(cash, oos_equity))
            record["folds"].append(
                {
                    "index": fold.index,
                    "train": _window_json(fold.train),
                    "test": _window_json(fold.test),
                    "embargo_days": fold.embargo_days,
                    "trials": trials,
                    "selected": {
                        "variant_index": chosen, "params": variants[chosen], "basis": basis,
                        "in_sample": trials[chosen]["is"], "out_of_sample": oos_stats,
                    },
                }
            )
            if on_fold_done is not None:
                on_fold_done(record)
    except ValidationCancelled:
        record["status"] = "cancelled"
        return record

    starting_cash = oos_windows[0][0] if oos_windows else base_effective.paper_starting_cash
    stitched = stitch_equity(oos_windows)
    rows = equity_rows(stitched)
    metrics = compute_metrics(rows, trade_rows(oos_trades), starting_cash, include_series=False)
    record["oos_equity"] = [{**point, "day": point["day"].isoformat()} for point in stitched]
    record["aggregate"] = {
        **headline(metrics),
        "trading_days": len(rows),
        "calendar_days": metrics["period"]["calendar_days"],
        "folds_positive": sum(1 for f in record["folds"] if (f["selected"]["out_of_sample"]["total_return_pct"] or 0) > 0),
        "folds": len(record["folds"]),
        "in_sample_sharpe_mean": _mean([f["selected"]["in_sample"]["sharpe"] for f in record["folds"]]),
        "out_of_sample_sharpe_mean": _mean([f["selected"]["out_of_sample"]["sharpe"] for f in record["folds"]]),
    }
    record["dsr"] = build_dsr(np.concatenate(oos_returns) if oos_returns else np.array([]), n_trials, len(variants), all_is_sharpes)
    record["status"] = "done"
    return record


def _window_json(window: Window) -> dict[str, Any]:
    return {"start": window.start.isoformat(), "end": window.end.isoformat(), "days": window.days}


def _mean(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _native_sharpe(result: BacktestResult, starting_cash: float) -> float | None:
    """Per-day Sharpe of a window (not annualised): the trial spread the deflated Sharpe ratio needs."""
    moments = sharpe_moments(daily_returns(starting_cash, equity_rows(result.equity)))
    return moments.sharpe if moments else None


# ---------------------------------------------------------------- the deflated Sharpe block


def build_dsr(
    oos_returns: np.ndarray, n_trials: int, n_variants: int, trial_native_sharpes: Sequence[float | None]
) -> dict[str, Any]:
    """The probabilistic and deflated Sharpe ratio of the stitched out-of-sample returns.

    `trial_native_sharpes` are the per-day in-sample Sharpe ratios of every train run (all
    variants, all folds): their spread is how far apart the things tried were, which sets how
    much luck the best of them is expected to show. With one variant there is no search, the
    expected luck is 0 and the deflated Sharpe ratio equals the probabilistic one."""
    moments = sharpe_moments(oos_returns)
    base: dict[str, Any] = {
        "n_trials": n_trials, "n_variants": n_variants, "confidence_level": DEFAULT_CONFIDENCE,
        "n_observations": int(len(oos_returns)),
    }
    if moments is None or moments.n < MIN_OOS_DAYS_FOR_DSR:
        reason = (
            "The out-of-sample returns are too few or too flat for a Sharpe ratio to mean anything "
            f"(needs at least {MIN_OOS_DAYS_FOR_DSR} trading days and some movement)."
        )
        return {**base, "available": False, "reason": reason}
    variance = trial_sharpe_variance(trial_native_sharpes) if n_trials > 1 else 0.0
    variance_known = variance is not None
    variance = variance or 0.0
    luck = expected_max_sharpe(variance, n_trials)
    psr = probabilistic_sharpe_ratio(moments.sharpe, 0.0, moments.n, moments.skewness, moments.kurtosis)
    dsr = deflated_sharpe_ratio(moments.sharpe, moments.n, n_trials, variance, moments.skewness, moments.kurtosis)
    min_trl = min_track_record_length(moments.sharpe, luck, moments.skewness, moments.kurtosis)
    out = {
        **base,
        "available": True,
        "sharpe_annualised": annualise(moments.sharpe),
        "skewness": moments.skewness,
        "kurtosis": moments.kurtosis,
        "trial_sharpe_std_annualised": annualise(math.sqrt(variance)),
        "trial_spread_known": variance_known or n_trials <= 1,
        "expected_max_sharpe_annualised": annualise(luck),
        "psr": psr,
        "dsr": dsr,
        "min_track_record_days": min_trl,
        "min_track_record_years": (min_trl / TRADING_DAYS_PER_YEAR) if min_trl is not None else None,
    }
    out["reading"] = dsr_reading(out)
    return out


def dsr_reading(dsr: dict[str, Any]) -> list[str]:
    """The result in plain English, as short sentences."""
    lines = [
        f"The out-of-sample Sharpe ratio is {dsr['sharpe_annualised']:.2f} a year, from {dsr['n_observations']} trading days."
    ]
    if dsr["n_trials"] > 1:
        lines.append(
            f"You tried {dsr['n_variants']} settings sets in {dsr['n_trials'] // dsr['n_variants']} folds ({dsr['n_trials']} tries). "
            f"With that many tries, luck alone is expected to produce a best Sharpe of about "
            f"{dsr['expected_max_sharpe_annualised']:.2f} a year."
        )
        if not dsr["trial_spread_known"]:
            lines.append("The tries' Sharpe ratios were too few to measure their spread, so no luck was subtracted: the deflation is understated.")
        lines.append(
            f"After subtracting that, the chance the strategy is genuinely better than luck is {dsr['dsr'] * 100:.0f}%."
        )
    else:
        lines.append(
            "Nothing was searched (a single settings set), so no luck is subtracted: the deflated figure equals the chance "
            f"the true Sharpe is above zero, {dsr['dsr'] * 100:.0f}%."
        )
    bar = int(DEFAULT_CONFIDENCE * 100)
    if dsr["dsr"] >= DEFAULT_CONFIDENCE:
        lines.append(f"That clears the usual {bar}% bar. It is evidence, not proof: see the notes under the scorecard.")
    else:
        lines.append(f"That is under the usual {bar}% bar, so this much Sharpe could plausibly be luck.")
    if dsr["min_track_record_years"] is not None:
        lines.append(
            f"At this Sharpe it would take about {dsr['min_track_record_years']:.1f} years of out-of-sample returns to reach {bar}%."
        )
    return lines


# ---------------------------------------------------------------- the scorecard


def build_validation_scorecard(result: dict[str, Any]) -> dict[str, Any]:
    """Two checklist lines and the honesty banners for a finished validation. Informational,
    like the run scorecard: nothing in the app acts on it."""
    aggregate, dsr = result.get("aggregate"), result.get("dsr")
    checks = []
    label = "Out-of-sample positive in most folds"
    criterion = f"more than half of the folds earn a positive out-of-sample return (needs {MIN_FOLDS_FOR_READING}+ folds)"
    if not aggregate:
        checks.append(_check("oos_folds", label, criterion, INSUFFICIENT, "n/a", "The validation did not finish."))
    else:
        positive, folds = aggregate["folds_positive"], aggregate["folds"]
        actual = f"{positive} of {folds} folds positive"
        if folds < MIN_FOLDS_FOR_READING:
            checks.append(_check("oos_folds", label, criterion, INSUFFICIENT, actual, f"Fewer than {MIN_FOLDS_FOR_READING} folds."))
        else:
            checks.append(_check("oos_folds", label, criterion, PASS if positive * 2 > folds else FAIL, actual))
    label = "Deflated Sharpe above 0.95"
    criterion = f"deflated Sharpe ratio of at least {DEFAULT_CONFIDENCE:g} for the number of tries"
    if not dsr or not dsr.get("available"):
        reason = (dsr or {}).get("reason") or "The validation did not finish."
        checks.append(_check("dsr", label, criterion, INSUFFICIENT, "n/a", reason))
    else:
        tries = f"{dsr['n_trials']} {'try' if dsr['n_trials'] == 1 else 'tries'}"
        detail = None if dsr["trial_spread_known"] else "The spread of the tries could not be measured, so the deflation is understated."
        checks.append(
            _check("dsr", label, criterion, PASS if dsr["dsr"] >= DEFAULT_CONFIDENCE else FAIL, f"{dsr['dsr']:.2f} after {tries}", detail)
        )
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in (PASS, FAIL, INSUFFICIENT)}
    return {
        "note": "Informational, not a verdict. Nothing in the app acts on these lines.",
        "checks": checks,
        "counts": {**counts, "total": len(checks)},
        "banners": _banners(result),
    }


def _banners(result: dict[str, Any]) -> list[dict[str, Any]]:
    n_trials = result.get("n_trials", 1)
    banners = [
        {
            "key": "survivorship", "level": "warning",
            "text": "Probably optimistic: the symbols are today's index members, so the test never held a company that was later "
            "dropped or went under.",
        },
        {
            "key": "price_only", "level": "warning",
            "text": "Price-only core: only the chart-based part of the score is tested; news, options, insiders, fundamentals and the AI are not included.",
        },
        {
            "key": "deflation", "level": "info",
            "text": (
                f"The deflated Sharpe ratio assumes the {n_trials} tries were independent. Settings next to each other are "
                "not, so it can understate the luck and so overstate the result."
                if n_trials > 1
                else "No settings were searched, so the deflated figure is the plain probability that the Sharpe is above zero."
            ),
        },
        {
            "key": "windows", "level": "info",
            "text": "Every window starts with the full starting cash and no open positions, and a position still open at the end of a "
            "window is valued at its last close. Short windows therefore have few trades.",
        },
    ]
    aggregate = result.get("aggregate")
    if aggregate is not None:
        n = aggregate.get("trade_count", 0)
        banners.append(
            {
                "key": "sample_size", "level": "warning" if n < 100 else "info",
                "text": f"{n} closed out-of-sample trades: " + ("few for reading a pattern." if n < 100 else "a workable sample."),
            }
        )
    return banners

