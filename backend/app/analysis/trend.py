from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from app.analysis.indicators import compute_support_resistance, ema, rate_of_change, rsi

Trend = Literal["Bullish", "Bearish", "Neutral"]
Momentum = Literal["Strong", "Weak"]

TREND_SLOPE_LOOKBACK = 5
MOMENTUM_ROC_LOOKBACK = 10
MOMENTUM_STRONG_THRESHOLD = 0.05
RSI_STRONG_BULL = 55
RSI_STRONG_BEAR = 45


def classify_trend(close: pd.Series, ema20: pd.Series, ema50: pd.Series) -> Trend:
    if len(close) <= TREND_SLOPE_LOOKBACK:
        return "Neutral"
    ema20_slope = ema20.iloc[-1] - ema20.iloc[-1 - TREND_SLOPE_LOOKBACK]
    if ema20.iloc[-1] > ema50.iloc[-1] and close.iloc[-1] > ema20.iloc[-1] and ema20_slope > 0:
        return "Bullish"
    if ema20.iloc[-1] < ema50.iloc[-1] and close.iloc[-1] < ema20.iloc[-1] and ema20_slope < 0:
        return "Bearish"
    return "Neutral"


def classify_momentum(close: pd.Series, rsi_series: pd.Series, trend: Trend) -> Momentum:
    roc = rate_of_change(close, MOMENTUM_ROC_LOOKBACK)
    rsi_val = float(rsi_series.iloc[-1])
    if trend == "Bullish" and roc > MOMENTUM_STRONG_THRESHOLD and rsi_val > RSI_STRONG_BULL:
        return "Strong"
    if trend == "Bearish" and roc < -MOMENTUM_STRONG_THRESHOLD and rsi_val < RSI_STRONG_BEAR:
        return "Strong"
    return "Weak"


@dataclass
class ChartAnalysis:
    price: float
    ema20: float
    ema50: float
    rsi14: float
    trend: Trend
    momentum: Momentum
    pct_from_ema20: float
    support: list[float]
    resistance: list[float]


def analyze_chart(df: pd.DataFrame) -> ChartAnalysis:
    """df must have columns open/high/low/close/volume, ascending by date."""
    close = df["close"]
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    rsi14 = rsi(close, 14)
    trend = classify_trend(close, ema20, ema50)
    momentum = classify_momentum(close, rsi14, trend)
    price = float(close.iloc[-1])
    sr = compute_support_resistance(df, price)
    return ChartAnalysis(
        price=price,
        ema20=float(ema20.iloc[-1]),
        ema50=float(ema50.iloc[-1]),
        rsi14=float(rsi14.iloc[-1]),
        trend=trend,
        momentum=momentum,
        pct_from_ema20=(price - float(ema20.iloc[-1])) / float(ema20.iloc[-1]),
        support=[lvl.price for lvl in sr.support],
        resistance=[lvl.price for lvl in sr.resistance],
    )
