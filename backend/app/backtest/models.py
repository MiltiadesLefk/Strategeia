"""Tables that keep backtest results in the main database.

A run's simulated plans, positions and equity live in a throwaway in-memory
database while it runs (see runner.py) and are copied here, in the small shapes
below, when it finishes. Nothing in these tables feeds the live app: the
dashboard, the portfolio stats and the trade-plan history never read them.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.timeutil import utcnow_naive

RUN_QUEUED = "queued"
RUN_RUNNING = "running"
RUN_DONE = "done"
RUN_FAILED = "failed"
RUN_CANCELLED = "cancelled"
RUN_STATUSES = (RUN_QUEUED, RUN_RUNNING, RUN_DONE, RUN_FAILED, RUN_CANCELLED)
ACTIVE_STATUSES = (RUN_QUEUED, RUN_RUNNING)


class BacktestRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    status: str = Field(default=RUN_QUEUED, index=True)
    # Everything that defines the run, as JSON: symbols, start/end, decision
    # cadence, the settings overrides asked for and the effective strategy
    # settings used (see runner.BacktestParams). Enough to repeat the run.
    params_json: str = "{}"
    # Fingerprint of the strategy the run used (the live rules' constants plus the
    # effective settings), the same hash app.strategy gives live plans.
    strategy_fingerprint: Optional[str] = None
    # Which parts of the score were active, and what the confidence bar means for
    # them (see coverage.py), as JSON.
    coverage_json: Optional[str] = None
    # The small set of numbers that sanity-check a run (see runner.summarize), as JSON.
    summary_json: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow_naive)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    # Progress, counted in simulated trading days.
    progress_days_done: int = 0
    progress_days_total: int = 0
    progress_date: Optional[date] = None
    cancel_requested: bool = False
    # The random-entry baseline that follows the main run (see baseline.py): which
    # part of the job is running ("main" | "baseline" | None once finished), how
    # many random runs are done of how many, and the finished random runs' headline
    # numbers as JSON. The statistics of the run itself are not stored: they are
    # recomputed from the rows below on every read (see metrics.py).
    progress_phase: Optional[str] = None
    baseline_seeds_done: Optional[int] = None
    baseline_seeds_total: Optional[int] = None
    baseline_json: Optional[str] = None


class BacktestTrade(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True, foreign_key="backtestrun.id")
    symbol: str
    direction: str
    # "closed", or "open" for a position still held when the run ended (valued at
    # the last close; it is left out of the win rate and the average R).
    status: str = "closed"
    entry_at: datetime
    entry_date: date
    entry_price: float
    planned_entry_price: Optional[float] = None
    stop_loss: float
    tp1: float
    shares: int
    exit_at: Optional[datetime] = None
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    close_reason: Optional[str] = None
    realized_pnl: Optional[float] = None
    realized_r: Optional[float] = None
    fees_paid: Optional[float] = None
    holding_days: Optional[int] = None  # trading days from entry to exit
    mfe_r: Optional[float] = None
    mae_r: Optional[float] = None
    mfe_pct: Optional[float] = None
    mae_pct: Optional[float] = None
    # What the plan scored at entry: the points that decided the trade.
    confidence_points: Optional[int] = None
    confidence_points_max: Optional[int] = None
    confidence_score: Optional[int] = None
    # Every score component of the plan as JSON (technical, market_confirmation,
    # vix_regime, ...), plus the plain-English reasons.
    scores_json: Optional[str] = None
    signal_reasons: Optional[str] = None


class BacktestEquityPoint(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True, foreign_key="backtestrun.id")
    day: date
    equity: float
    cash: float
    open_positions: int = 0
