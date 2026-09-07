from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.trend import analyze_chart, classify_momentum, classify_trend


def test_classify_trend_bullish_when_price_above_rising_emas():
    close = pd.Series([10, 11, 12, 13, 14, 15, 16])
    ema20 = pd.Series([9, 9.2, 9.5, 9.8, 10.1, 10.5, 11.0])
    ema50 = pd.Series([8, 8, 8, 8, 8, 8, 8])
    assert classify_trend(close, ema20, ema50) == "Bullish"


def test_classify_trend_bearish_when_price_below_falling_emas():
    close = pd.Series([16, 15, 14, 13, 12, 11, 10])
    ema20 = pd.Series([17, 16.5, 16.0, 15.5, 15.0, 14.5, 14.0])
    ema50 = pd.Series([18, 18, 18, 18, 18, 18, 18])
    assert classify_trend(close, ema20, ema50) == "Bearish"


def test_classify_trend_neutral_when_emas_are_flat_and_crossed():
    close = pd.Series([10, 10, 10, 10, 10, 10, 10])
    ema20 = pd.Series([10, 10, 10, 10, 10, 10, 10])
    ema50 = pd.Series([10, 10, 10, 10, 10, 10, 10])
    assert classify_trend(close, ema20, ema50) == "Neutral"


def test_classify_momentum_strong_bullish():
    close = pd.Series([100.0] * 10 + [110.0])  # >5% 10-bar ROC
    rsi_series = pd.Series([60.0] * 11)
    assert classify_momentum(close, rsi_series, "Bullish") == "Strong"


def test_classify_momentum_weak_bullish_when_roc_small():
    close = pd.Series([100.0] * 10 + [101.0])  # 1% ROC, below threshold
    rsi_series = pd.Series([60.0] * 11)
    assert classify_momentum(close, rsi_series, "Bullish") == "Weak"


def test_analyze_chart_on_synthetic_uptrend():
    n = 150
    closes = 100 + np.arange(n) * 0.5 + 2 * np.sin(np.arange(n) / 7)
    df = pd.DataFrame(
        {
            "open": closes - 0.3,
            "high": closes + 1.0,
            "low": closes - 1.0,
            "close": closes,
            "volume": [1_000_000.0] * n,
        }
    )
    chart = analyze_chart(df)
    assert chart.trend == "Bullish"
    assert chart.momentum in ("Strong", "Weak")
    assert isinstance(chart.support, list)
    assert isinstance(chart.resistance, list)
