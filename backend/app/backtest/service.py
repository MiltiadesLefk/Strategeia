"""Running backtests as background jobs and keeping their results.

One run at a time, in one worker thread: a run is CPU-bound Python and a second
one would only slow both down. The thread reads the price history from disk
(never the network), runs runner.run_backtest on a throwaway in-memory database,
then copies the trades and the daily equity into the BacktestRun / BacktestTrade /
BacktestEquityPoint tables of the main database. The only things a run writes to
the real database are those three tables.

A run is not resumable. If the app stops while one is running it is marked failed
("interrupted") at the next start (recover_interrupted_runs), never left
"running" forever.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.backtest.coverage import describe_coverage
from app.backtest.data_provider import PriceBook
from app.backtest.models import (
    ACTIVE_STATUSES,
    RUN_CANCELLED,
    RUN_DONE,
    RUN_FAILED,
    RUN_QUEUED,
    RUN_RUNNING,
    BacktestEquityPoint,
    BacktestRun,
    BacktestTrade,
)
from app.backtest.params import (
    BENCHMARK_SYMBOLS,
    BacktestInputError,
    BacktestParams,
    effective_settings,
    strategy_fingerprint,
)
from app.backtest.baseline import run_baseline
from app.backtest.runner import params_to_json, run_backtest
from app.config import AppSettings
from app.data_providers.history_store import HistoryStore
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

# Progress is written to the run row about this many times per run, not once per
# simulated day.
PROGRESS_UPDATES_PER_RUN = 200
INTERRUPTED_MESSAGE = "interrupted: the app stopped or restarted while this run was in progress"


class BacktestBusyError(RuntimeError):
    """Another run is queued or running."""


class BacktestNotFoundError(LookupError):
    pass


def recover_interrupted_runs(session_factory: Callable[[], Session]) -> int:
    """Mark every run left queued/running by a previous process as failed. Returns how many."""
    with session_factory() as session:
        stuck = session.exec(select(BacktestRun).where(BacktestRun.status.in_(ACTIVE_STATUSES))).all()
        for run in stuck:
            run.status = RUN_FAILED
            run.error = INTERRUPTED_MESSAGE
            run.finished_at = utcnow_naive()
            session.add(run)
        session.commit()
        return len(stuck)


class BacktestManager:
    """Starts, tracks and cancels runs. One instance serves the whole app."""

    def __init__(self, session_factory: Callable[[], Session], store_getter: Callable[[], HistoryStore]):
        self._session_factory = session_factory
        self._store_getter = store_getter
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._active_id: int | None = None

    # ---- starting ----------------------------------------------------------

    def _check_benchmarks(self, params: BacktestParams) -> None:
        """Fail fast, before a run row exists, when the history the strategy
        needs is plainly missing."""
        params.validate_dates()
        store = self._store_getter()
        for symbol in BENCHMARK_SYMBOLS:
            coverage = store.coverage(symbol)
            if coverage is None:
                raise BacktestInputError(
                    f"price history for {symbol} is not stored: run scripts/preload_history.py --benchmarks first"
                )
            if coverage.last_date < params.end:
                raise BacktestInputError(
                    f"{symbol} history ends {coverage.last_date}, before the requested end {params.end}: "
                    "refresh it with scripts/preload_history.py"
                )

    def start(self, params: BacktestParams, base_settings: AppSettings, *, background: bool = True) -> int:
        """Queue a run and start its worker. Raises BacktestBusyError while another
        is active and BacktestInputError for a request that cannot work."""
        self._check_benchmarks(params)
        settings = effective_settings(base_settings, params.overrides)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise BacktestBusyError("a backtest is already running: wait for it or cancel it first")
            with self._session_factory() as session:
                active = session.exec(select(BacktestRun).where(BacktestRun.status.in_(ACTIVE_STATUSES))).first()
                if active is not None:
                    raise BacktestBusyError(f"backtest #{active.id} is still {active.status}")
                run = BacktestRun(
                    status=RUN_QUEUED,
                    params_json=json.dumps({"symbols": params.symbols, "start": params.start.isoformat(),
                                            "end": params.end.isoformat()}),
                    strategy_fingerprint=strategy_fingerprint(settings),
                    coverage_json=json.dumps(describe_coverage(settings.min_confidence_for_trade, base_settings.min_confidence_for_trade)),
                )
                session.add(run)
                session.commit()
                session.refresh(run)
                run_id = run.id
            self._cancel.clear()
            self._active_id = run_id
            self._thread = threading.Thread(
                target=self._work, args=(run_id, params, settings, base_settings.min_confidence_for_trade), name=f"backtest-{run_id}", daemon=True
            )
            if background:
                self._thread.start()
        if not background:
            self._work(run_id, params, settings, base_settings.min_confidence_for_trade)
        return run_id

    def cancel(self, run_id: int) -> str:
        """Ask a run to stop. A queued/running run stops at its next simulated day
        (what it did so far is kept). Returns the run's status after the request."""
        with self._session_factory() as session:
            run = session.get(BacktestRun, run_id)
            if run is None:
                raise BacktestNotFoundError(f"no backtest #{run_id}")
            if run.status not in ACTIVE_STATUSES:
                return run.status
            run.cancel_requested = True
            session.add(run)
            session.commit()
        if self._active_id == run_id:
            self._cancel.set()
        return "cancelling"

    def wait(self, timeout: float | None = None) -> None:
        """Block until the worker finishes (tests)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def is_busy(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    # ---- the worker --------------------------------------------------------

    def _update(self, run_id: int, **fields: Any) -> None:
        with self._session_factory() as session:
            run = session.get(BacktestRun, run_id)
            if run is None:
                return
            for key, value in fields.items():
                setattr(run, key, value)
            session.add(run)
            session.commit()

    def _work(self, run_id: int, params: BacktestParams, settings: AppSettings, live_min_confidence: int) -> None:
        try:
            self._update(run_id, status=RUN_RUNNING, started_at=utcnow_naive(), progress_phase="main")
            store = self._store_getter()
            book = PriceBook.load(store, [*params.symbols, *BENCHMARK_SYMBOLS], params.start, params.end)
            self._update(run_id, params_json=params_to_json(params, settings, book))

            step = {"next": 0}

            def progress(done: int, total: int, day: date) -> None:
                if done >= step["next"] or done == total:
                    step["next"] = done + max(1, total // PROGRESS_UPDATES_PER_RUN)
                    self._update(run_id, progress_days_done=done, progress_days_total=total, progress_date=day)

            result = run_backtest(
                params, settings, book, progress=progress, should_cancel=self._cancel.is_set
            )
            coverage = describe_coverage(settings.min_confidence_for_trade, live_min_confidence)
            # The random-entry baseline follows a main run that finished (a request that
            # does not carry the baseline fields, e.g. a direct call, gets none).
            baseline_runs = getattr(params, "baseline_runs", 0) if getattr(params, "run_baseline", False) else 0
            with_baseline = baseline_runs > 0 and result.status == "done"
            self._save_result(run_id, result, coverage, finish=not with_baseline)
            if with_baseline:
                self._run_baseline(run_id, params, settings, book, result.summary, baseline_runs)
        except BacktestInputError as exc:
            self._update(run_id, status=RUN_FAILED, error=str(exc), finished_at=utcnow_naive())
        except Exception as exc:  # noqa: BLE001 - a run must always end in a final state
            logger.exception("Backtest #%s failed", run_id)
            self._update(run_id, status=RUN_FAILED, error=f"{type(exc).__name__}: {exc}", finished_at=utcnow_naive())
        finally:
            self._active_id = None

    def _run_baseline(
        self, run_id: int, params: BacktestParams, settings: AppSettings, book: PriceBook, summary: dict[str, Any], runs: int
    ) -> None:
        """The random-entry baseline, K seeded random runs after the main one. The main
        result is already saved, so a failure here (or a cancel) keeps it: the run ends
        "done" with the baseline marked failed or cancelled and the seeds finished so far."""
        self._update(
            run_id, progress_phase="baseline", baseline_seeds_done=0, baseline_seeds_total=runs,
            progress_days_done=0,
        )
        step = {"seed": 0, "next": 0}

        def progress(seed: int, total_seeds: int, done: int, total: int) -> None:
            if step["seed"] != seed:
                step.update(seed=seed, next=0)
            if done >= step["next"] or done == total:
                step["next"] = done + max(1, total // PROGRESS_UPDATES_PER_RUN)
                self._update(run_id, progress_days_done=done, progress_days_total=total, baseline_seeds_done=seed - 1)

        def seed_done(record: dict[str, Any]) -> None:
            self._update(run_id, baseline_json=json.dumps(record), baseline_seeds_done=len(record["seeds"]))

        try:
            record = run_baseline(
                params, settings, book, summary, runs=runs, progress=progress, on_seed_done=seed_done,
                should_cancel=self._cancel.is_set,
            )
        except Exception as exc:  # noqa: BLE001 - the real run's result stands; say what went wrong with the baseline
            logger.exception("Backtest #%s baseline failed", run_id)
            with self._session_factory() as session:
                previous = session.get(BacktestRun, run_id).baseline_json
            record = json.loads(previous) if previous else {"requested_runs": runs, "seeds": []}
            record.update(status="failed", note=f"The baseline stopped early: {type(exc).__name__}: {exc}")
        self._update(
            run_id, baseline_json=json.dumps(record), baseline_seeds_done=len(record.get("seeds", [])),
            status=RUN_DONE, finished_at=utcnow_naive(), progress_phase=None,
            progress_days_done=summary["days_simulated"], progress_days_total=summary["days_requested"],
        )

    def _save_result(self, run_id: int, result, coverage: dict[str, Any], finish: bool = True) -> None:
        with self._session_factory() as session:
            for trade in result.trades:
                session.add(
                    BacktestTrade(
                        run_id=run_id,
                        symbol=trade["symbol"], direction=trade["direction"], status=trade["status"],
                        entry_at=trade["entry_at"], entry_date=trade["entry_date"],
                        entry_price=trade["entry_price"], planned_entry_price=trade["planned_entry_price"],
                        stop_loss=trade["stop_loss"], tp1=trade["tp1"], shares=trade["shares"],
                        exit_at=trade.get("exit_at"), exit_date=trade.get("exit_date"),
                        exit_price=trade.get("exit_price"), close_reason=trade.get("close_reason"),
                        realized_pnl=trade.get("realized_pnl"), realized_r=trade.get("realized_r"),
                        fees_paid=trade.get("fees_paid"), holding_days=trade.get("holding_days"),
                        mfe_r=trade["mfe_r"], mae_r=trade["mae_r"], mfe_pct=trade["mfe_pct"], mae_pct=trade["mae_pct"],
                        confidence_points=trade["confidence_points"],
                        confidence_points_max=trade["confidence_points_max"],
                        confidence_score=trade["confidence_score"],
                        scores_json=json.dumps(trade["scores"]), signal_reasons=trade["signal_reasons"],
                    )
                )
            for point in result.equity:
                session.add(
                    BacktestEquityPoint(
                        run_id=run_id, day=point["day"], equity=point["equity"], cash=point["cash"],
                        open_positions=point["open_positions"],
                    )
                )
            run = session.get(BacktestRun, run_id)
            # finish=False: the random-entry baseline still has to run, so the run stays
            # "running" (its results are readable already: summary_json is set).
            if finish:
                run.status = RUN_CANCELLED if result.status == "cancelled" else RUN_DONE
                run.finished_at = utcnow_naive()
                run.progress_phase = None
            run.summary_json = json.dumps(result.summary)
            run.coverage_json = json.dumps(coverage)
            run.progress_days_done = result.summary["days_simulated"]
            run.progress_days_total = result.summary["days_requested"]
            session.add(run)
            session.commit()


# ---------------------------------------------------------------- reading


def get_run(session: Session, run_id: int) -> BacktestRun:
    run = session.get(BacktestRun, run_id)
    if run is None:
        raise BacktestNotFoundError(f"no backtest #{run_id}")
    return run


def list_runs(session: Session, limit: int = 50) -> list[BacktestRun]:
    return list(session.exec(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(limit)).all())


def run_trades(session: Session, run_id: int) -> list[BacktestTrade]:
    get_run(session, run_id)
    return list(session.exec(select(BacktestTrade).where(BacktestTrade.run_id == run_id).order_by(BacktestTrade.id)).all())


def run_equity(session: Session, run_id: int) -> list[BacktestEquityPoint]:
    get_run(session, run_id)
    return list(
        session.exec(select(BacktestEquityPoint).where(BacktestEquityPoint.run_id == run_id).order_by(BacktestEquityPoint.day)).all()
    )


_default_manager: BacktestManager | None = None
_default_guard = threading.Lock()


def get_backtest_manager() -> BacktestManager:
    """The process-wide manager, on the real database and the real history store."""
    global _default_manager
    with _default_guard:
        if _default_manager is None:
            from app.data_providers.history_store import get_history_store
            from app.database import engine

            _default_manager = BacktestManager(lambda: Session(engine), get_history_store)
        return _default_manager


def reset_backtest_manager() -> None:
    """Forget the process-wide manager (tests)."""
    global _default_manager
    with _default_guard:
        _default_manager = None


def recover_on_startup() -> int:
    from app.database import engine

    return recover_interrupted_runs(lambda: Session(engine))
