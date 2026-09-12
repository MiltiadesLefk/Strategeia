"""Parsing tests for NasdaqProvider — no network.

Nasdaq returns display strings rather than numbers ("$332.27", "50,716,997",
"N/A", "$344.5699/$226.65"), so the parsing layer is where this provider will
break, not the HTTP layer.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.data_providers.base import DataProviderError
from app.data_providers.cache import clear_cache
from app.data_providers.nasdaq_provider import (
    NasdaqProvider,
    _asset_class,
    _split_range,
    _to_float,
)


@pytest.fixture(autouse=True)
def _isolate_cache():
    """get_ohlcv/get_quote are @cached, and the cache key is (provider, method,
    args) — without this, the first test's frame is served to every later test
    asking for the same symbol."""
    clear_cache()
    yield
    clear_cache()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$332.27", 332.27),
        ("50,716,997", 50716997.0),
        ("+1.75%", 1.75),
        ("4,849,208,188,600", 4849208188600.0),
        (332.27, 332.27),
        ("$1.08", 1.08),
    ],
)
def test_to_float_parses_display_strings(raw, expected):
    assert _to_float(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["N/A", "", None, "--", "UNCH", "abc"])
def test_to_float_returns_none_for_missing_values(raw):
    """None, never 0.0 — a missing market cap must not read as a real zero."""
    assert _to_float(raw) is None


def test_split_range_reads_high_then_low():
    high, low = _split_range("$344.5699/$226.65")
    assert high == pytest.approx(344.5699)
    assert low == pytest.approx(226.65)


def test_split_range_handles_absent_range():
    assert _split_range("N/A") == (None, None)
    assert _split_range(None) == (None, None)


@pytest.mark.parametrize(
    "symbol,expected",
    [("AAPL", "stocks"), ("BTC-USD", "crypto"), ("^VIX", "index")],
)
def test_asset_class_derived_from_symbol_shape(symbol, expected):
    assert _asset_class(symbol) == expected


def _chart_payload(rows):
    return {"chart": [{"z": r} for r in rows]}


def test_get_ohlcv_parses_and_sorts_bars(monkeypatch):
    provider = NasdaqProvider()
    payload = _chart_payload(
        [
            {"open": "327.45", "high": "336.22", "low": "326.30", "close": "332.27",
             "volume": "50,716,870", "dateTime": "9/11/2026"},
            {"open": "316.67", "high": "326.74", "low": "316.51", "close": "326.57",
             "volume": "70,011,910", "dateTime": "9/10/2026"},
        ]
    )
    monkeypatch.setattr(NasdaqProvider, "_get", lambda self, path, params: payload)

    frame = provider.get_ohlcv("AAPL", period="1y", interval="1d")

    assert list(frame.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert len(frame) == 2
    assert frame["date"].is_monotonic_increasing  # ascending, as analyze_chart requires
    assert frame["close"].iloc[-1] == pytest.approx(332.27)
    assert pd.api.types.is_numeric_dtype(frame["volume"])


def test_get_ohlcv_drops_bars_with_missing_prices(monkeypatch):
    """A half-formed bar is worse than a missing one — it would feed a real
    high/low into the exit scan."""
    provider = NasdaqProvider()
    payload = _chart_payload(
        [
            {"open": "327.45", "high": "336.22", "low": "326.30", "close": "332.27",
             "volume": "1", "dateTime": "9/11/2026"},
            {"open": "N/A", "high": "N/A", "low": "N/A", "close": "N/A",
             "volume": "0", "dateTime": "9/12/2026"},
        ]
    )
    monkeypatch.setattr(NasdaqProvider, "_get", lambda self, path, params: payload)

    frame = provider.get_ohlcv("AAPL", period="1y", interval="1d")

    assert len(frame) == 1


def test_get_ohlcv_raises_when_nothing_usable(monkeypatch):
    provider = NasdaqProvider()
    monkeypatch.setattr(NasdaqProvider, "_get", lambda self, path, params: {"chart": []})
    with pytest.raises(DataProviderError):
        provider.get_ohlcv("AAPL", period="1y", interval="1d")


def test_intraday_and_weekly_fall_through_to_the_next_provider():
    """NotImplementedError is CompositeDataProvider's 'try the next one' signal;
    raising DataProviderError here would wrongly mark Nasdaq as broken."""
    provider = NasdaqProvider()
    with pytest.raises(NotImplementedError):
        provider.get_ohlcv("AAPL", period="1d", interval="5m")
    with pytest.raises(NotImplementedError):
        provider.get_ohlcv("AAPL", period="2y", interval="1wk")


def test_unmapped_period_falls_through():
    provider = NasdaqProvider()
    with pytest.raises(NotImplementedError):
        provider.get_ohlcv("AAPL", period="7mo", interval="1d")


def test_negative_change_keeps_its_sign(monkeypatch):
    """_to_float strips the sign along with the '%', so the provider has to
    restore it — otherwise every down day reads as an up day."""
    provider = NasdaqProvider()

    def fake_get(self, path, params):
        if path.endswith("/info"):
            return {"primaryData": {"lastSalePrice": "$218.29", "volume": "89,099,170",
                                    "percentageChange": "-0.53%"}}
        return {"summaryData": {"AverageVolume": {"value": "1,000,000"}}}

    monkeypatch.setattr(NasdaqProvider, "_get", fake_get)
    quote = provider.get_quote("NVDA")

    assert quote.change_pct_24h == pytest.approx(-0.53)
    assert quote.price == pytest.approx(218.29)
    assert quote.avg_volume_20d == pytest.approx(1_000_000)


def test_quote_survives_a_failed_summary_call(monkeypatch):
    """avg_volume feeds volume_ratio, which degrades to 1.0 upstream — not
    worth failing the whole quote over."""
    provider = NasdaqProvider()

    def fake_get(self, path, params):
        if path.endswith("/info"):
            return {"primaryData": {"lastSalePrice": "$100.00", "volume": "1,000",
                                    "percentageChange": "+1.00%"}}
        raise DataProviderError("summary down")

    monkeypatch.setattr(NasdaqProvider, "_get", fake_get)
    quote = provider.get_quote("AAPL")

    assert quote.price == pytest.approx(100.0)
    assert quote.avg_volume_20d == 0.0
