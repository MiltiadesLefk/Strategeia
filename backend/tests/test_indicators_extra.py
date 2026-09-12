"""Bollinger / MACD / session VWAP / ATR.

The VWAP cases are the ones that matter: a naive implementation runs one
cumulative sum across the whole series, which on multi-month data is an
anchored VWAP wearing VWAP's name.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analysis.indicators import atr, bollinger, latest_atr, macd, session_vwap, sma


def _frame(rows):
    return pd.DataFrame(rows)


def test_sma_has_no_value_before_its_window_fills():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    result = sma(s, 3)
    assert pd.isna(result.iloc[0]) and pd.isna(result.iloc[1])
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[3] == pytest.approx(3.0)


def test_bollinger_uses_population_std_not_sample_std():
    """pandas defaults to ddof=1; every charting package uses ddof=0. With
    ddof=1 the bands come out visibly wider than TradingView's for the same
    settings, which makes 'price touched the band' mean something different."""
    close = pd.Series([float(x) for x in range(1, 21)])
    bands = bollinger(close, period=20, mult=2.0)
    expected_std = np.std(close.to_numpy(), ddof=0)
    middle = close.mean()
    assert bands.middle.iloc[-1] == pytest.approx(middle)
    assert bands.upper.iloc[-1] == pytest.approx(middle + 2 * expected_std)
    assert bands.lower.iloc[-1] == pytest.approx(middle - 2 * expected_std)


def test_bollinger_bands_straddle_the_middle():
    close = pd.Series(np.random.default_rng(7).normal(100, 3, 80))
    bands = bollinger(close)
    tail = slice(-30, None)
    assert (bands.upper[tail] >= bands.middle[tail]).all()
    assert (bands.lower[tail] <= bands.middle[tail]).all()


def test_macd_histogram_is_line_minus_signal():
    close = pd.Series(np.linspace(50, 150, 120))
    result = macd(close)
    assert result.histogram.iloc[-1] == pytest.approx(result.macd.iloc[-1] - result.signal.iloc[-1])


def test_macd_is_positive_in_a_sustained_uptrend():
    close = pd.Series(np.linspace(50, 150, 120))
    assert macd(close).macd.iloc[-1] > 0


def test_session_vwap_resets_each_day():
    """Two sessions with very different prices. If VWAP carried across the day
    boundary, day 2's first bar would be dragged toward day 1's prices."""
    rows = []
    for day, price in ((1, 100.0), (2, 200.0)):
        for _ in range(3):
            rows.append(
                {"date": pd.Timestamp(f"2026-09-0{day} 10:00"), "high": price, "low": price,
                 "close": price, "volume": 1000.0}
            )
    # give each bar a distinct timestamp within its session
    frame = _frame(rows)
    frame["date"] = [
        pd.Timestamp("2026-09-01 10:00"), pd.Timestamp("2026-09-01 11:00"), pd.Timestamp("2026-09-01 12:00"),
        pd.Timestamp("2026-09-02 10:00"), pd.Timestamp("2026-09-02 11:00"), pd.Timestamp("2026-09-02 12:00"),
    ]

    result = session_vwap(frame)

    assert result.iloc[2] == pytest.approx(100.0)  # end of day 1
    assert result.iloc[3] == pytest.approx(200.0)  # first bar of day 2 — reset, not blended
    assert result.iloc[5] == pytest.approx(200.0)


def test_session_vwap_weights_by_volume():
    frame = _frame(
        [
            {"date": pd.Timestamp("2026-09-01 10:00"), "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0},
            {"date": pd.Timestamp("2026-09-01 11:00"), "high": 200.0, "low": 200.0, "close": 200.0, "volume": 3.0},
        ]
    )
    # (100*1 + 200*3) / 4 = 175, not the 150 an unweighted mean would give
    assert session_vwap(frame).iloc[-1] == pytest.approx(175.0)


def test_session_vwap_survives_a_zero_volume_bar():
    frame = _frame(
        [
            {"date": pd.Timestamp("2026-09-01 10:00"), "high": 100.0, "low": 100.0, "close": 100.0, "volume": 0.0},
            {"date": pd.Timestamp("2026-09-01 11:00"), "high": 110.0, "low": 110.0, "close": 110.0, "volume": 5.0},
        ]
    )
    result = session_vwap(frame)
    assert not np.isinf(result.to_numpy()).any()
    assert result.iloc[-1] == pytest.approx(110.0)


def test_atr_accounts_for_gaps_not_just_the_intraday_range():
    """True range includes |high - prev_close|, so a bar that gaps away and
    then trades quietly still registers as a volatile bar. A plain high-low
    range would call it calm — which is exactly when a tight stop gets hit."""
    quiet = [{"high": 101.0, "low": 100.0, "close": 100.5} for _ in range(20)]
    gapped = quiet + [{"high": 121.0, "low": 120.0, "close": 120.5}]
    calm_atr = latest_atr(_frame(quiet), period=14)
    gap_atr = latest_atr(_frame(gapped), period=14)
    assert gap_atr > calm_atr


def test_latest_atr_returns_none_without_enough_bars():
    assert latest_atr(_frame([{"high": 1.0, "low": 0.5, "close": 0.8}]), period=14) is None


def test_atr_is_positive_for_real_data():
    rng = np.random.default_rng(3)
    closes = 100 + np.cumsum(rng.normal(0, 1, 60))
    frame = _frame([{"high": c + 1, "low": c - 1, "close": c} for c in closes])
    assert atr(frame).iloc[-1] > 0
