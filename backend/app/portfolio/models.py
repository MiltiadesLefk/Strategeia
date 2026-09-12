from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from sqlmodel import Field, SQLModel

from app.timeutil import utcnow_naive

TradePlanStatus = Literal["pending", "executed", "discarded", "no_trade"]
PositionStatus = Literal["open", "closed"]
CloseReason = Literal["stop_hit", "tp1_hit", "manual"]


class TradePlanRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str
    # Null direction/entry/stop/targets/sizing mean the engine looked at this
    # symbol and explicitly decided NOT to trade it (status="no_trade") —
    # see `reason` — rather than every evaluation forcing a tradeable plan
    # regardless of quality. See notes/Decisions.md.
    direction: Optional[str] = None
    entry: Optional[float] = None
    stop: Optional[float] = None
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    rr1: Optional[float] = None
    rr2: Optional[float] = None
    suggested_shares: Optional[int] = None
    account_risk_dollars: Optional[float] = None
    confidence_score: int
    ai_take_text: Optional[str] = None
    ai_provider: Optional[str] = None
    reason: Optional[str] = None
    # Independent AI opinion (Settings -> AI Trading Overlay, off by
    # default) — a second, separately-labeled read from the SAME raw data,
    # never blended into direction/confidence_score above. Null unless the
    # overlay was on AND a real LLM provider was configured for this
    # evaluation. See notes/Decisions.md.
    ai_opinion_stance: Optional[str] = None
    ai_opinion_score: Optional[int] = None
    ai_opinion_text: Optional[str] = None
    # The AI's own read of headline substance — distinct from `ai_opinion_text`
    # (the overall trade opinion) because it's the one place the app lets the
    # AI genuinely analyze news instead of the rule-based engine's plain
    # keyword matching (fundamental_scoring.score_news_sentiment). See
    # notes/Decisions.md.
    ai_news_assessment: Optional[str] = None
    time_horizon: str = "1-4 weeks"
    status: str = "pending"
    created_at: datetime = Field(default_factory=utcnow_naive)
    # "Smart" signal breakdown, added alongside the technical scanner score —
    # see analysis/fundamental_scoring.py. Nullable so old rows (generated
    # before this field existed) just read back as None, not an error.
    technical_score: Optional[int] = None
    fundamental_score: Optional[int] = None
    news_score: Optional[int] = None
    # Weekly-timeframe + broad-market (SPY) agreement, +/-2 total — see
    # analysis/market_confirmation.py. Deliberately separate from
    # technical_score (scanner_scoring's own 0-6) rather than folded into
    # it, so the Market Scanner's score/signal tiers stay unaffected.
    market_confirmation_score: Optional[int] = None
    # VIX regime flag, -1 or 0 only (never positive — see
    # market_confirmation.score_vix_regime) and options put/call
    # positioning skew, +/-1 (analysis/options_scoring.py). Both minor,
    # independently-capped factors, same "shown, never hidden" pattern.
    vix_regime_score: Optional[int] = None
    options_score: Optional[int] = None
    # Open-market insider buying, +/-1 (analysis/insider_scoring.py). Same
    # "shown, never hidden" pattern; nullable so pre-existing rows read None.
    insider_score: Optional[int] = None
    signal_reasons: Optional[str] = None  # "; "-joined, human-readable — not JSON, kept simple


class PaperPosition(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    trade_plan_id: Optional[int] = Field(default=None, foreign_key="tradeplanrecord.id")
    symbol: str
    direction: str
    entry_price: float
    # What the plan asked for, vs entry_price = what it actually filled at
    # (current market + slippage — see PaperTradingEngine._resolve_entry_price).
    # Nullable: rows written before the engine re-quoted at open read back None.
    planned_entry_price: Optional[float] = None
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
    # Round-trip commission, charged at open and again at close. realized_pnl
    # is already net of it; kept separately so the UI can show gross vs net.
    fees_paid: Optional[float] = None


class EquitySnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=utcnow_naive)
    equity_value: float
    cash_balance: float


class AccountState(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    starting_cash: float
    current_cash: float
