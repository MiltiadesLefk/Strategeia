"""The table that keeps walk-forward validations (see validation.py).

One row per validation: its request, its progress while it runs, and its whole
result (folds, out-of-sample curve, deflated Sharpe) as JSON. The individual
window runs are not stored as backtests: they are throwaway, and only the numbers
the result needs are kept. Nothing in this table feeds the live app.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.backtest.models import RUN_QUEUED
from app.timeutil import utcnow_naive


class BacktestValidation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    status: str = Field(default=RUN_QUEUED, index=True)  # queued | running | done | failed | cancelled
    params_json: str = "{}"
    strategy_fingerprint: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow_naive)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    cancel_requested: bool = False
    # Progress is counted in window runs (train runs of every variant plus one test run per fold).
    progress_runs_done: int = 0
    progress_runs_total: int = 0
    progress_label: Optional[str] = None
    progress_days_done: int = 0
    progress_days_total: int = 0
    result_json: Optional[str] = None
