from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.data_providers.base import AllProvidersFailedError
from app.data_providers.base import AllProvidersFailedError
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


def test_get_analysis_intraday_range_uses_full_datetime_strings():
    """1D/1W ranges take a different code path (no display-window trimming,
    full ISO datetime instead of a bare date) — the frontend chart relies on
    this to distinguish intraday from daily bars."""
    result = get_analysis("AAPL", FakeProvider(), NullLLMProvider(), range_="1d")
    assert len(result.candles) == 80  # fake ignores period/interval, returns all fetched bars
    assert "T" in result.candles[0].date
    assert "T" in result.ema20_series[0].date


class _NoIntradayProvider(FakeProvider):
    """Daily bars work; intraday is rate-limited (the one source for it is down)."""

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        if interval != "1d":
            raise AllProvidersFailedError("yfinance: 429 Too Many Requests")
        return super().get_ohlcv(symbol, period=period, interval=interval)


def test_intraday_range_fails_loudly_instead_of_serving_daily_bars_as_intraday():
    """With no intraday source answering, 1D/1W must raise (the API turns that into a
    502 the chart's fallback reacts to). Quietly returning daily bars under a 1D
    label would draw a wrong chart with no warning."""
    provider = _NoIntradayProvider()
    for range_ in ("1d", "1w"):
        with pytest.raises(AllProvidersFailedError):
            get_analysis("AAPL", provider, NullLLMProvider(), range_=range_)
    # The daily view the UI falls back to still works against the same provider.
    assert len(get_analysis("AAPL", provider, NullLLMProvider(), range_="1mo").candles) == 21


def test_analysis_endpoint_returns_502_when_intraday_is_unavailable():
    from fastapi.testclient import TestClient

    from app.api.deps import get_data_provider
    from app.main import app

    app.dependency_overrides[get_data_provider] = lambda: _NoIntradayProvider()
    try:
        client = TestClient(app)
        resp = client.get("/api/analysis/AAPL?range=1d")
        assert resp.status_code == 502
        assert "No data available" in resp.json()["detail"]
        assert client.get("/api/analysis/AAPL?range=1mo").status_code == 200
    finally:
        app.dependency_overrides.pop(get_data_provider, None)
