from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.config import AppSettings
from app.data_providers.base import DataProviderError
from app.data_providers.cache import clear_cache
from app.data_providers.factory import get_data_provider
from app.data_providers.stockanalysis_provider import StockAnalysisProvider

GET = "app.data_providers.stockanalysis_provider.httpx.get"


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    clear_cache()
    yield
    clear_cache()


class FakeJsonResponse:
    def __init__(self, rows, status_code=200):
        self._rows = rows
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return {"status": self.status_code, "data": self._rows}


def _rows():
    today = date.today()
    return [
        {"t": (today - timedelta(days=1)).isoformat(), "o": 103, "h": 105, "l": 102, "c": 104, "a": 103.9, "v": 1300000},
        {"t": (today - timedelta(days=10)).isoformat(), "o": 100, "h": 102, "l": 99, "c": 101, "a": 100.9, "v": 1200000},
        {"t": (today - timedelta(days=300)).isoformat(), "o": 90, "h": 91, "l": 89, "c": 90.5, "a": 90.4, "v": 1000000},
        {"t": "bad"},
    ]


def test_parses_sorts_and_filters_by_period(monkeypatch):
    monkeypatch.setattr(GET, lambda *a, **k: FakeJsonResponse(_rows()))
    df = StockAnalysisProvider().get_ohlcv("AAPL", "6mo")
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert df["close"].tolist() == [101.0, 104.0]


def test_falls_back_to_etf_path_on_404(monkeypatch):
    urls = []

    def fake_get(url, **kwargs):
        urls.append(url)
        return FakeJsonResponse(None, 404) if "/s/" in url else FakeJsonResponse(_rows())

    monkeypatch.setattr(GET, fake_get)
    assert len(StockAnalysisProvider().get_ohlcv("SPY", "6mo")) == 2
    assert "/s/" in urls[0] and "/e/" in urls[1]


def test_raises_when_no_data(monkeypatch):
    monkeypatch.setattr(GET, lambda *a, **k: FakeJsonResponse(None, 404))
    with pytest.raises(DataProviderError):
        StockAnalysisProvider().get_ohlcv("ZZZZ")


def test_not_implemented_cases():
    provider = StockAnalysisProvider()
    for kwargs in ({"symbol": "AAPL", "interval": "15m"}, {"symbol": "BTC-USD"}, {"symbol": "^VIX"}):
        with pytest.raises(NotImplementedError):
            provider.get_ohlcv(**kwargs)
    with pytest.raises(NotImplementedError):
        provider.get_quote("AAPL")


def test_factory_uses_stockanalysis_and_skips_stooq_unless_enabled():
    names = [p.name for p in get_data_provider(AppSettings())._providers]
    assert names.index("yfinance") < names.index("nasdaq") < names.index("stockanalysis")
    assert "stooq" not in names
    on = [p.name for p in get_data_provider(AppSettings(stooq_enabled=True))._providers]
    assert "stooq" in on
