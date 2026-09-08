from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from sqlmodel import Field, SQLModel

from app.timeutil import utcnow_naive

TradePlanStatus = Literal["pending", "executed", "discarded"]
PositionStatus = Literal["open", "closed"]
CloseReason = Literal["stop_hit", "tp1_hit", "manual"]


class TradePlanRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str
    direction: str
    entry: float
    stop: float
    tp1: float
    tp2: float
    rr1: float
    rr2: float
    suggested_shares: int
    account_risk_dollars: float
    confidence_score: int
    ai_take_text: str
    ai_provider: str
    time_horizon: str = "1-4 weeks"
    status: str = "pending"
    created_at: datetime = Field(default_factory=utcnow_naive)
    # "Smart" signal breakdown, added alongside the technical scanner score —
    # see analysis/fundamental_scoring.py. Nullable so old rows (generated
    # before this field existed) just read back as None, not an error.
    technical_score: Optional[int] = None
    fundamental_score: Optional[int] = None
    news_score: Optional[int] = None
    signal_reasons: Optional[str] = None  # "; "-joined, human-readable — not JSON, kept simple


class PaperPosition(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    trade_plan_id: Optional[int] = Field(default=None, foreign_key="tradeplanrecord.id")
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    shares: int
    opened_at: datetime = Field(default_factory=utcnow_naive)
    status: str = "open"
    closed_at: Optional[datetime] = None
    close_price: Optional[float] = None
    close_reason: Optional[str] = None
    realized_pnl: Optional[float] = None
    realized_r: Optional[float] = None


class EquitySnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=utcnow_naive)
    equity_value: float
    cash_balance: float


class AccountState(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    starting_cash: float
    current_cash: float
