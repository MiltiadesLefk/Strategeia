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


class OpenPositionRequest(BaseModel):
    trade_plan_id: int


class ClosePositionRequest(BaseModel):
    reason: str = "manual"


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


class EquityPointSchema(BaseModel):
    timestamp: UtcDatetime
    equity_value: float
    cash_balance: float
