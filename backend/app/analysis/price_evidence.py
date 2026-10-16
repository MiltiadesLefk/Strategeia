"""Evidence read straight from price and volume bars (no outside data), signed for the trade's direction.

  * Relative strength: how the stock did against the market (SPY) over the last RELATIVE_STRENGTH_DAYS
    trading days. Beating it by RELATIVE_STRENGTH_GAP_PCT points or more supports a long (leaders keep
    leading); lagging it by that much supports a short.
  * Volume trend: who has been trading it, accumulation or distribution. Over the last VOLUME_TREND_DAYS
    bars, the volume traded on up days divided by the volume traded on down days. At ACCUMULATION_RATIO or
    more, buyers have been the heavier side (supports a long); at its inverse or less, sellers have (supports
    a short). This is the sustained version of the single-day volume spike the chart score already has.

Each is capped at one point either way and scores 0 with too little data. They use only bars that were final
at the moment, so a backtest can rebuild them (they are price-only evidence).
"""

from __future__ import annotations

import pandas as pd

RELATIVE_STRENGTH_SCORE_CAP = 1
RELATIVE_STRENGTH_DAYS = 63
RELATIVE_STRENGTH_GAP_PCT = 5.0

VOLUME_TREND_SCORE_CAP = 1
VOLUME_TREND_DAYS = 20
ACCUMULATION_RATIO = 1.3


def _period_return_pct(close: pd.Series, days: int) -> float | None:
    if close is None or len(close) <= days:
        return None
    start = float(close.iloc[-1 - days])
    return (float(close.iloc[-1]) / start - 1) * 100 if start > 0 else None


def relative_strength(ohlcv: pd.DataFrame | None, market_ohlcv: pd.DataFrame | None, days: int = RELATIVE_STRENGTH_DAYS):
    """(stock return %, market return %, gap in points) over `days` bars, or None when either has too few."""
    if ohlcv is None or market_ohlcv is None:
        return None
    stock, market = _period_return_pct(ohlcv["close"], days), _period_return_pct(market_ohlcv["close"], days)
    if stock is None or market is None:
        return None
    return stock, market, stock - market


def score_relative_strength(
    direction: str | None, ohlcv: pd.DataFrame | None, market_ohlcv: pd.DataFrame | None
) -> tuple[int, list[str]]:
    reading = relative_strength(ohlcv, market_ohlcv)
    if reading is None or direction not in ("long", "short"):
        return 0, []
    stock, market, gap = reading
    if abs(gap) < RELATIVE_STRENGTH_GAP_PCT:
        return 0, []
    leading = gap > 0
    supports = leading == (direction == "long")
    verb = "supports" if supports else "argues against"
    word = "ahead of" if leading else "behind"
    return (1 if supports else -1) * RELATIVE_STRENGTH_SCORE_CAP, [
        f"{RELATIVE_STRENGTH_DAYS}-day return {stock:+.1f}% is {abs(gap):.1f} points {word} the market ({market:+.1f}%), which {verb} a {direction}"
    ]


def up_down_volume_ratio(ohlcv: pd.DataFrame | None, days: int = VOLUME_TREND_DAYS) -> float | None:
    """Volume on up days over volume on down days across the last `days` bars; None when there are no down
    days with volume (a ratio would be meaningless)."""
    if ohlcv is None or len(ohlcv) < days + 1:
        return None
    tail = ohlcv.iloc[-(days + 1):]
    change = tail["close"].diff().iloc[1:]
    volume = tail["volume"].iloc[1:]
    up, down = float(volume[change > 0].sum()), float(volume[change < 0].sum())
    if down <= 0 or up <= 0:
        return None
    return up / down


def score_volume_trend(direction: str | None, ohlcv: pd.DataFrame | None) -> tuple[int, list[str]]:
    ratio = up_down_volume_ratio(ohlcv)
    if ratio is None or direction not in ("long", "short"):
        return 0, []
    if ratio >= ACCUMULATION_RATIO:
        accumulating = True
    elif ratio <= 1 / ACCUMULATION_RATIO:
        accumulating = False
    else:
        return 0, []
    supports = accumulating == (direction == "long")
    verb = "supports" if supports else "argues against"
    name = "accumulation (buyers heavier)" if accumulating else "distribution (sellers heavier)"
    return (1 if supports else -1) * VOLUME_TREND_SCORE_CAP, [
        f"volume trend: {name}, up-day volume is {ratio:.2f}x down-day volume over {VOLUME_TREND_DAYS} days, which {verb} a {direction}"
    ]
