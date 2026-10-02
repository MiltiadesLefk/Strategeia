from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class PositionSchema(BaseModel):
    id: int
    trade_plan_id: int | None
    symbol: str
    direction: str
    entry_price: float
    planned_entry_price: float | None = None
    stop_loss: float
    tp1: float
    tp2: float
    shares: int
    opened_at: UtcDatetime
    status: str
    closed_at: UtcDatetime | None
    close_price: float | None
    close_reason: str | None
    realized_pnl: float | None
    realized_r: float | None
    fees_paid: float | None = None
    # The strategy version of the plan this position came from (read through
    # the plan, never stored on the position); None for a manual position with
    # no plan and for plans from before versioning existed.
    strategy_version: int | None = None
    # Best/worst price during the trade (MFE/MAE), non-negative: how far it went in the
    # trade's favour / against it, as % of entry and in R (multiples of |entry - stop|).
    # Stored for a closed position (None if never recorded); computed live for an open one.
    mfe_pct: float | None = None
    mae_pct: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    # How the exit was placed in time: see PaperPosition.exit_resolution / entry_day_check.
    exit_resolution: str | None = None
    entry_day_check: str | None = None
    # The AI-written lesson for a closed trade (see services/lesson_service.py).
    lesson_text: str | None = None
    lesson_provider: str | None = None
    lesson_model: str | None = None
    lesson_at: UtcDatetime | None = None
    lesson_error: str | None = None
    # The key of the sleeve (paper account) that holds the position; "core" for
    # the original account and for positions from before sleeves existed.
    sleeve_key: str | None = None


class OpenPositionRequest(BaseModel):
    trade_plan_id: int


class ClosePositionRequest(BaseModel):
    reason: str = "manual"


class ExcursionGroupSchema(BaseModel):
    n: int = 0
    avg_mfe_r: float | None = None
    avg_mae_r: float | None = None


class ExcursionStatsSchema(BaseModel):
    """MFE/MAE averages in R over closed trades that have the figures (`measured`)."""

    closed_trades: int = 0
    measured: int = 0
    winners: ExcursionGroupSchema = ExcursionGroupSchema()
    losers: ExcursionGroupSchema = ExcursionGroupSchema()
    exit_efficiency: float | None = None
    exit_efficiency_n: int = 0
    losers_reached_1r: int = 0
    winners_near_stop: int = 0


class PortfolioStatsSchema(BaseModel):
    total_trades: int
    win_rate: float
    total_return: float
    avg_rr: float | None
    active_positions: int
    portfolio_value: float
    starting_cash: float
    current_cash: float
    exit_reasons: dict[str, int] = {}
    excursions: ExcursionStatsSchema = ExcursionStatsSchema()


class EquityPointSchema(BaseModel):
    timestamp: UtcDatetime
    equity_value: float
    cash_balance: float
