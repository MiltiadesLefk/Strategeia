from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    result = 100 - (100 / (1 + rs))
    return result.fillna(100)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's Average True Range. True range is the widest of (high-low),
    (high-prev_close), (prev_close-low) so it accounts for overnight gaps,
    not just the intraday range. Smoothed with the same ewm(alpha=1/period)
    Wilder smoothing rsi() above already uses, for consistency."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def latest_atr(df: pd.DataFrame, period: int = 14) -> float | None:
    """Most recent ATR value, or None when there aren't enough bars for a
    meaningful read — callers treat None as "no volatility opinion" and fall
    back to their non-ATR path rather than guessing."""
    if len(df) < period + 1:
        return None
    value = atr(df, period).iloc[-1]
    if pd.isna(value) or value <= 0:
        return None
    return float(value)


def rate_of_change(close: pd.Series, periods: int) -> float:
    if len(close) <= periods or close.iloc[-1 - periods] == 0:
        return 0.0
    return float((close.iloc[-1] - close.iloc[-1 - periods]) / close.iloc[-1 - periods])


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


@dataclass
class BollingerBands:
    upper: pd.Series
    middle: pd.Series
    lower: pd.Series


def bollinger(close: pd.Series, period: int = 20, mult: float = 2.0) -> BollingerBands:
    """Middle band is a 20-period SMA, outer bands +/- `mult` standard
    deviations. Uses the population std (ddof=0), which is the convention
    every charting package follows — pandas defaults to the sample std
    (ddof=1) and would draw visibly wider bands than TradingView for the
    same settings."""
    middle = sma(close, period)
    deviation = close.rolling(window=period, min_periods=period).std(ddof=0)
    return BollingerBands(upper=middle + mult * deviation, middle=middle, lower=middle - mult * deviation)


@dataclass
class MacdResult:
    macd: pd.Series
    signal: pd.Series
    histogram: pd.Series


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> MacdResult:
    """Standard 12/26/9. The signal line is an EMA of the MACD line, and the
    histogram is their difference — positive means momentum is building in
    the MACD line's direction."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return MacdResult(macd=macd_line, signal=signal_line, histogram=macd_line - signal_line)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-weighted average price, reset at the start of each session.

    VWAP is an *intraday* measure: it answers "what has the average buyer paid
    since the open today". Running the cumulative sum across a multi-month
    series instead — which is what a naive implementation does — produces a
    line anchored to whenever the data happened to start, drifting further
    from meaning with every bar. That is a different indicator (anchored VWAP)
    wearing VWAP's name, so this resets per calendar day and callers only plot
    it on intraday ranges. See analysis_service.get_analysis.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3
    volume = df["volume"].fillna(0.0)
    session = pd.to_datetime(df["date"]).dt.date
    cum_pv = (typical * volume).groupby(session).cumsum()
    cum_vol = volume.groupby(session).cumsum()
    return (cum_pv / cum_vol.replace(0, float("nan"))).ffill()


def find_pivot_highs(df: pd.DataFrame, left: int = 3, right: int = 3) -> list[tuple[int, float]]:
    """Fractal pivot highs: high[i] strictly greater than the `left`+`right`
    neighboring bars. The most recent `right` bars can't produce a confirmed
    pivot yet (not enough lookahead) and are skipped."""
    highs = df["high"].to_numpy()
    pivots: list[tuple[int, float]] = []
    for i in range(left, len(highs) - right):
        window = highs[i - left : i + right + 1]
        if highs[i] == window.max() and (window == highs[i]).sum() == 1:
            pivots.append((i, float(highs[i])))
    return pivots


def find_pivot_lows(df: pd.DataFrame, left: int = 3, right: int = 3) -> list[tuple[int, float]]:
    lows = df["low"].to_numpy()
    pivots: list[tuple[int, float]] = []
    for i in range(left, len(lows) - right):
        window = lows[i - left : i + right + 1]
        if lows[i] == window.min() and (window == lows[i]).sum() == 1:
            pivots.append((i, float(lows[i])))
    return pivots


@dataclass
class Level:
    price: float
    strength: int


def cluster_levels(prices: list[float], tolerance_pct: float = 0.005) -> list[Level]:
    if not prices:
        return []
    ordered = sorted(prices)
    clusters: list[list[float]] = [[ordered[0]]]
    for price in ordered[1:]:
        cluster_mean = sum(clusters[-1]) / len(clusters[-1])
        if abs(price - cluster_mean) / cluster_mean <= tolerance_pct:
            clusters[-1].append(price)
        else:
            clusters.append([price])
    return [Level(price=sum(c) / len(c), strength=len(c)) for c in clusters]


@dataclass
class SupportResistance:
    support: list[Level]
    resistance: list[Level]


def compute_support_resistance(
    df: pd.DataFrame, current_price: float, lookback: int = 120, left: int = 3, right: int = 3
) -> SupportResistance:
    window = df.tail(lookback)
    pivot_highs = [p for _, p in find_pivot_highs(window, left, right)]
    pivot_lows = [p for _, p in find_pivot_lows(window, left, right)]

    resistance_clusters = sorted(
        (lvl for lvl in cluster_levels(pivot_highs) if lvl.price > current_price),
        key=lambda lvl: lvl.price - current_price,
    )
    support_clusters = sorted(
        (lvl for lvl in cluster_levels(pivot_lows) if lvl.price < current_price),
        key=lambda lvl: current_price - lvl.price,
    )
    return SupportResistance(support=support_clusters[:2], resistance=resistance_clusters[:2])
