"""Assembling the read-only reports for a stored run: metrics, benchmarks, baseline,
scorecard, and what price history is held for a run's symbols.

Everything here reads rows and bars and writes nothing. A run's statistics are
recomputed on every call from its stored equity points and trades, so they can
never drift from them.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlmodel import Session

from app.backtest import service
from app.backtest.baseline import read_baseline
from app.backtest.benchmarks import BarsLoader, build_benchmarks, store_loader
from app.backtest.metrics import compute_metrics, equity_rows, headline, trade_rows
from app.backtest.models import BacktestRun
from app.backtest.params import BENCHMARK_SYMBOLS
from app.backtest.scorecard import Criteria, build_scorecard

DEFAULT_STARTING_CASH = 100_000.0
# A preload command naming more symbols than this is too long to be useful; it
# points at --sp500 instead.
MAX_SYMBOLS_IN_COMMAND = 30
# The strategy reads about a year of daily bars before a symbol is evaluated.
WARMUP_NOTE = "A symbol is not evaluated until it has about a year (252 trading days) of history behind the decision day."


class RunNotReadyError(LookupError):
    """The run has no results yet (still queued, or it failed before finishing)."""


def starting_cash_of(run: BacktestRun, summary: dict[str, Any]) -> float:
    if summary.get("starting_cash"):
        return float(summary["starting_cash"])
    params = json.loads(run.params_json or "{}")
    return float((params.get("effective_settings") or {}).get("paper_starting_cash") or DEFAULT_STARTING_CASH)


def _ready(session: Session, run_id: int) -> tuple[BacktestRun, dict[str, Any]]:
    run = service.get_run(session, run_id)
    if not run.summary_json:
        raise RunNotReadyError(
            f"backtest #{run_id} has no results yet (status: {run.status})" + (f": {run.error}" if run.error else "")
        )
    return run, json.loads(run.summary_json)


def metrics_for_run(session: Session, run_id: int) -> dict[str, Any]:
    run, summary = _ready(session, run_id)
    equity = equity_rows(service.run_equity(session, run_id))
    trades = trade_rows(service.run_trades(session, run_id))
    metrics = compute_metrics(equity, trades, starting_cash_of(run, summary))
    metrics["run_status"] = run.status
    metrics["partial"] = bool(summary.get("cancelled"))
    return metrics


def benchmarks_for_run(session: Session, run_id: int, loader: BarsLoader, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    run, summary = _ready(session, run_id)
    metrics = metrics or metrics_for_run(session, run_id)
    equity = equity_rows(service.run_equity(session, run_id))
    params = json.loads(run.params_json or "{}")
    return build_benchmarks(equity, starting_cash_of(run, summary), metrics, params.get("symbols", []), loader)


def baseline_for_run(session: Session, run_id: int, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    run, _ = _ready(session, run_id)
    stored = json.loads(run.baseline_json) if run.baseline_json else None
    if stored is None:
        params = json.loads(run.params_json or "{}")
        reason = (
            "this run was started without a random-entry baseline"
            if not params.get("run_baseline")
            else "the random-entry baseline has not produced a result (yet)"
        )
        return {"available": False, "reason": reason}
    metrics = metrics or metrics_for_run(session, run_id)
    return read_baseline(stored, headline(metrics))


def scorecard_for_run(
    session: Session, run_id: int, loader: BarsLoader, criteria: Criteria | None = None
) -> dict[str, Any]:
    run, _ = _ready(session, run_id)
    metrics = metrics_for_run(session, run_id)
    benchmarks = benchmarks_for_run(session, run_id, loader, metrics)
    baseline = baseline_for_run(session, run_id, metrics)
    coverage = json.loads(run.coverage_json) if run.coverage_json else None
    params = json.loads(run.params_json or "{}")
    return build_scorecard(metrics, benchmarks, baseline, coverage, params, criteria)


# ---------------------------------------------------------------- what history is held


def preload_command(symbols: list[str]) -> str:
    """The exact command that fills the history the strategy needs for `symbols`."""
    shown = symbols[:MAX_SYMBOLS_IN_COMMAND]
    tail = " ... (or --sp500 for the whole bundled list)" if len(symbols) > MAX_SYMBOLS_IN_COMMAND else ""
    names = " ".join(shown)
    return f"python scripts/preload_history.py {names + ' ' if names else ''}--benchmarks{tail}"


def history_coverage(store, symbols: list[str]) -> dict[str, Any]:
    """What the local history store holds for `symbols` and the two benchmarks every
    run needs. The backtester never downloads at run time: whatever is missing here
    has to be preloaded first."""

    def describe(symbol: str) -> dict[str, Any]:
        coverage = store.coverage(symbol) if store is not None else None
        if coverage is None:
            return {"symbol": symbol, "stored": False, "first_date": None, "last_date": None, "bars": 0}
        return {
            "symbol": symbol, "stored": True, "first_date": coverage.first_date, "last_date": coverage.last_date,
            "bars": coverage.bar_count,
        }

    benchmarks = [describe(s) for s in BENCHMARK_SYMBOLS]
    rows = [describe(s) for s in symbols]
    missing = [r["symbol"] for r in rows if not r["stored"]]
    benchmark_missing = [b["symbol"] for b in benchmarks if not b["stored"]]
    last_dates: list[date] = [b["last_date"] for b in benchmarks if b["last_date"]]
    return {
        "benchmarks": benchmarks,
        "symbols": rows,
        "missing": missing,
        "benchmarks_missing": benchmark_missing,
        # The newest end date a run can use: both benchmarks must reach it.
        "latest_end_date": min(last_dates) if len(last_dates) == len(BENCHMARK_SYMBOLS) else None,
        "preload_command": preload_command(missing) if (missing or benchmark_missing) else None,
        "warmup_note": WARMUP_NOTE,
    }


def default_bars_loader() -> BarsLoader:
    """A loader over the process-wide history store that does not create the store's
    file just to be asked (reading a run's benchmarks must not write anything)."""
    from app.config import InfraSettings
    from app.data_providers.history_store import get_history_store

    def load(symbol: str, start: date, end: date):
        if not InfraSettings().history_db_file.exists():
            raise FileNotFoundError("no price history has been stored yet")
        return store_loader(get_history_store())(symbol, start, end)

    return load


def default_store_for_coverage():
    """The history store, or None when its file does not exist yet."""
    from app.config import InfraSettings
    from app.data_providers.history_store import get_history_store

    return get_history_store() if InfraSettings().history_db_file.exists() else None
