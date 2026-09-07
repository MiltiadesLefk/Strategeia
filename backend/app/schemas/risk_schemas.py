from __future__ import annotations

from pydantic import BaseModel


class RiskCalculateRequest(BaseModel):
    symbol: str
    account_size: float
    risk_pct: float
    entry: float
    stop: float
    direction: str = "long"


class RiskCalculateResponse(BaseModel):
    shares: int
    risk_per_share: float
    account_risk_dollars: float
    position_value: float
    capped_by_cash: bool
    tp1: float
    tp2: float
    rr1: float
    rr2: float
    potential_gain: float
    potential_risk: float
