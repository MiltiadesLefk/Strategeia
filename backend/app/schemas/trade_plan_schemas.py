from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TradePlanGenerateRequest(BaseModel):
    symbol: str
    account_size: float | None = None
    risk_pct: float | None = None


class TradePlanResponse(BaseModel):
    id: int | None = None
    symbol: str
    direction: str | None
    reason: str | None = None
    entry: float | None = None
    stop: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    rr1: float | None = None
    rr2: float | None = None
    suggested_shares: int | None = None
    account_risk_dollars: float | None = None
    potential_gain: float | None = None
    potential_risk: float | None = None
    confidence_score: int | None = None
    time_horizon: str | None = None
    ai_take_text: str | None = None
    ai_provider: str | None = None
    status: str | None = None
    created_at: datetime | None = None
