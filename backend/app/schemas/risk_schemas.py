from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RiskCalculateRequest(BaseModel):
    symbol: str
    account_size: float = Field(gt=0)
    risk_pct: float = Field(gt=0, le=100)
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    direction: Literal["long", "short"] = "long"


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
