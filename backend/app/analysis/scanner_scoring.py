from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.analysis.trend import ChartAnalysis

Signal = Literal["potential_setup", "watching", "no_signal"]
Direction = Literal["long", "short"] | None

VOLUME_RATIO_THRESHOLD = 1.5
NEAR_LEVEL_PCT = 0.02
RSI_OVEREXTENDED_LONG = 75
RSI_OVEREXTENDED_SHORT = 25
POTENTIAL_SETUP_SCORE = 4
WATCHING_SCORE = 2


@dataclass
class ScanResult:
    symbol: str
    price: float
    change_pct_24h: float
    signal: Signal
    score: int
    direction: Direction
    trend: str
    momentum: str


def _direction_from_trend(trend: str) -> Direction:
    if trend == "Bullish":
        return "long"
    if trend == "Bearish":
        return "short"
    return None


def _near_key_level(price: float, direction: Direction, chart: ChartAnalysis) -> bool:
    levels = chart.resistance if direction == "long" else chart.support
    return any(abs(level - price) / price <= NEAR_LEVEL_PCT for level in levels)


def score_symbol(
    symbol: str, price: float, change_pct_24h: float, chart: ChartAnalysis, volume_ratio: float
) -> ScanResult:
    direction = _direction_from_trend(chart.trend)
    score = 0

    if direction is not None:
        score += 2
        if chart.momentum == "Strong":
            score += 2
        if volume_ratio > VOLUME_RATIO_THRESHOLD:
            score += 1
        if _near_key_level(price, direction, chart):
            score += 1
        if direction == "long" and chart.rsi14 > RSI_OVEREXTENDED_LONG:
            score -= 1
        if direction == "short" and chart.rsi14 < RSI_OVEREXTENDED_SHORT:
            score -= 1

    if score >= POTENTIAL_SETUP_SCORE:
        signal: Signal = "potential_setup"
    elif score >= WATCHING_SCORE:
        signal = "watching"
    else:
        signal = "no_signal"

    return ScanResult(
        symbol=symbol,
        price=price,
        change_pct_24h=change_pct_24h,
        signal=signal,
        score=score,
        direction=direction,
        trend=chart.trend,
        momentum=chart.momentum,
    )
