from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TradePlanGenerateRequest(BaseModel):
    symbol: str
    account_size: float | None = Field(default=None, gt=0)
    risk_pct: float | None = Field(default=None, gt=0, le=100)


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
    technical_score: int | None = None
    fundamental_score: int | None = None
    news_score: int | None = None
    market_confirmation_score: int | None = None
    vix_regime_score: int | None = None
    options_score: int | None = None
    signal_reasons: str | None = None
    ai_opinion_stance: str | None = None
    ai_opinion_score: int | None = None
    ai_opinion_text: str | None = None
    ai_news_assessment: str | None = None
