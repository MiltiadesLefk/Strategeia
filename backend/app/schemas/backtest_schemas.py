from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

from app.backtest.params import BacktestParams
from app.schemas.common import UtcDatetime

# The request body is the validated run definition itself (symbols, start, end,
# decision_every_n_days, overrides), so the API and the runner can never disagree
# about what a valid run is.
BacktestRequest = BacktestParams


class BacktestCreatedResponse(BaseModel):
    id: int
    status: str


class BacktestProgress(BaseModel):
    days_done: int
    days_total: int
    current_date: date | None = None


class BacktestRunSchema(BaseModel):
    id: int
    status: str  # queued | running | done | failed | cancelled
    created_at: UtcDatetime
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    error: str | None = None
    cancel_requested: bool
    progress: BacktestProgress
    strategy_fingerprint: str | None = None
    # Everything that defines the run (symbols, dates, overrides, effective settings, history held).
    params: dict[str, Any]
    # Which score parts were active and what the confidence bar means for them.
    coverage: dict[str, Any] | None = None
    # Trade count, win rate, total return, average R, final equity, max drawdown, days simulated,
    # symbols with data / skipped. Null until the run finishes.
    summary: dict[str, Any] | None = None


class BacktestTradeSchema(BaseModel):
    id: int
    symbol: str
    direction: str
    status: str  # closed | open (held when the run ended)
    entry_at: UtcDatetime
    entry_date: date
    entry_price: float
    planned_entry_price: float | None = None
    stop_loss: float
    tp1: float
    shares: int
    exit_at: UtcDatetime | None = None
    exit_date: date | None = None
    exit_price: float | None = None
    close_reason: str | None = None  # stop_hit | tp1_hit | time_exit | open_at_end
    realized_pnl: float | None = None
    realized_r: float | None = None
    fees_paid: float | None = None
    holding_days: int | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    confidence_points: int | None = None
    confidence_points_max: int | None = None
    confidence_score: int | None = None
    scores: dict[str, Any]
    signal_reasons: str | None = None


class BacktestEquityPointSchema(BaseModel):
    day: date
    equity: float
    cash: float
    open_positions: int
