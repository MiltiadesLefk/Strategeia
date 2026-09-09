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


class ScanJobStatus(BaseModel):
    job_id: str
    status: str
    progress: int
    total: int
    results: list[ScanResultSchema] | None = None


class AutoScanResponse(BaseModel):
    generated: list[str]
    no_trade: list[str] = []
