from __future__ import annotations

import numpy as np
import pandas as pd

from app.llm_providers.null_provider import NullLLMProvider
from app.services.analysis_service import get_analysis


class FakeProvider:
    name = "fake"

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        n = 80
        dates = pd.date_range("2026-01-01", periods=n, freq="D")
        closes = 100 + np.arange(n) * 0.3
        return pd.DataFrame(
            {
                "date": dates,
                "open": closes - 0.1,
                "high": closes + 1,
                "low": closes - 1,
                "close": closes,
                "volume": [1_000_000.0] * n,
            }
        )


def test_get_analysis_returns_ema_series_aligned_with_candles():
    result = get_analysis("AAPL", FakeProvider(), NullLLMProvider())
    # default range "3mo" -> displays the last 63 of the 80 fetched bars
    assert len(result.candles) == 63
    assert len(result.ema20_series) == 63
    assert len(result.ema50_series) == 63
    assert result.ema20_series[-1].date == result.candles[-1].date
    assert result.ai_provider == "none"


def test_get_analysis_display_range_trims_view_not_indicators():
    full = get_analysis("AAPL", FakeProvider(), NullLLMProvider(), range_="1y")
    trimmed = get_analysis("AAPL", FakeProvider(), NullLLMProvider(), range_="1mo")
    assert len(full.candles) == 80  # capped by the fake's 80 fetched bars
    assert len(trimmed.candles) == 21
    # trend/rsi are computed off the full fetched history either way
    assert full.trend == trimmed.trend
    assert full.rsi14 == trimmed.rsi14
