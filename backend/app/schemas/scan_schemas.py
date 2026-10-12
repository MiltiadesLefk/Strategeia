from __future__ import annotations

from pydantic import BaseModel


class ScanResultSchema(BaseModel):
    symbol: str
    price: float
    change_pct_24h: float
    signal: str
    score: int
    direction: str | None
    trend: str
    momentum: str
    sparkline: list[float] = []


class ScanResponse(BaseModel):
    results: list[ScanResultSchema]
    errors: list[str] = []


class AutoScanResponse(BaseModel):
    generated: list[str]
    no_trade: list[str] = []
