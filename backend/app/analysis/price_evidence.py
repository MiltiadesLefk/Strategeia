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

