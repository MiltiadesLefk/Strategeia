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
    # ATR14 in price terms, plus the same value as a share of price — the
    # second is what makes it comparable across instruments ("this name moves
    # 2.4% on an average day").
    atr14: float | None = None
    atr_pct: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    candles: list[CandleSchema]
    ema20_series: list[SeriesPoint]
    ema50_series: list[SeriesPoint]
    bollinger_upper_series: list[SeriesPoint] = []
    bollinger_lower_series: list[SeriesPoint] = []
    rsi_series: list[SeriesPoint] = []
    macd_series: list[SeriesPoint] = []
    macd_signal_series: list[SeriesPoint] = []
    macd_histogram_series: list[SeriesPoint] = []
    # Empty on daily ranges by design: VWAP is an intraday measure and a
    # cumulative run across months is a different indicator wearing its name.
    # See analysis/indicators.session_vwap.
    vwap_series: list[SeriesPoint] = []
    insight_text: str
    ai_provider: str
    ai_error: str | None = None
