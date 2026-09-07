from __future__ import annotations

from pydantic import BaseModel


class CandleSchema(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class SeriesPoint(BaseModel):
    date: str
    value: float


class AnalysisResponse(BaseModel):
    symbol: str
    price: float
    ema20: float
    ema50: float
    rsi14: float
    trend: str
    momentum: str
    support: list[float]
    resistance: list[float]
    candles: list[CandleSchema]
    ema20_series: list[SeriesPoint]
    ema50_series: list[SeriesPoint]
    insight_text: str
    ai_provider: str
    ai_error: str | None = None
