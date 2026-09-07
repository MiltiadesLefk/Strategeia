from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.indicators import cluster_levels, ema, find_pivot_highs, find_pivot_lows, rsi


def test_ema_matches_pandas_ewm():
    series = pd.Series([10.0, 11.0, 12.0, 11.5, 13.0, 14.0, 13.5, 15.0])
    result = ema(series, span=3)
    expected = series.ewm(span=3, adjust=False).mean()
    pd.testing.assert_series_equal(result, expected)


def test_rsi_is_100_for_strictly_increasing_series():
    series = pd.Series([float(i) for i in range(1, 31)])
    result = rsi(series, period=14)
    assert result.iloc[-1] == 100.0


def test_rsi_is_low_for_strictly_decreasing_series():
    series = pd.Series([float(i) for i in range(30, 0, -1)])
    result = rsi(series, period=14)
    assert result.iloc[-1] < 5.0


def test_find_pivot_highs_detects_single_peak():
    highs = [1, 2, 3, 4, 5, 10, 5, 4, 3, 2, 1]
    df = pd.DataFrame({"high": highs, "low": [h - 1 for h in highs]})
    pivots = find_pivot_highs(df, left=3, right=3)
    assert (5, 10.0) in pivots


def test_find_pivot_lows_detects_single_trough():
    lows = [10, 9, 8, 7, 6, 1, 6, 7, 8, 9, 10]
    df = pd.DataFrame({"low": lows, "high": [l + 1 for l in lows]})
    pivots = find_pivot_lows(df, left=3, right=3)
    assert (5, 1.0) in pivots


def test_cluster_levels_merges_nearby_prices():
    levels = cluster_levels([100.0, 100.2, 100.4, 150.0], tolerance_pct=0.01)
    assert len(levels) == 2
    assert levels[0].strength == 3
    assert levels[1].strength == 1


def test_cluster_levels_empty_input():
    assert cluster_levels([]) == []
