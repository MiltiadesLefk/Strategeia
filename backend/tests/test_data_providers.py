from __future__ import annotations

from datetime import date, timedelta

import httpx
import pandas as pd
import pytest

from app.data_providers.base import AllProvidersFailedError, DataProviderError, QuoteData
from app.data_providers.cache import clear_cache
from app.data_providers.composite_provider import CompositeDataProvider
from app.data_providers.stooq_provider import StooqProvider


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    clear_cache()
    yield
    clear_cache()


def _make_stooq_csv() -> str:
    today = date.today()
    rows = [
        (today - timedelta(days=400), 90, 91, 89, 90.5, 1_000_000),  # outside the 6mo window
        (today - timedelta(days=10), 100, 102, 99, 101, 1_200_000),
        (today - timedelta(days=1), 103, 105, 102, 104, 1_300_000),
    ]
    header = "Date,Open,High,Low,Close,Volume"
    lines = [header] + [f"{d.isoformat()},{o},{h},{l},{c},{v}" for d, o, h, l, c, v in rows]
    return "\n".join(lines)


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        pass


def test_stooq_provider_parses_and_filters_by_period(monkeypatch):
    monkeypatch.setattr(
        "app.data_providers.stooq_provider.httpx.get",
        lambda *args, **kwargs: FakeResponse(_make_stooq_csv()),
    )
    provider = StooqProvider()
    df = provider.get_ohlcv("AAPL", period="6mo", interval="1d")

    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert len(df) == 2  # the 400-day-old row is filtered out


def test_stooq_provider_raises_on_empty_response(monkeypatch):
    monkeypatch.setattr(
        "app.data_providers.stooq_provider.httpx.get", lambda *args, **kwargs: FakeResponse("")
    )
    provider = StooqProvider()
    with pytest.raises(DataProviderError):
        provider.get_ohlcv("ZZZZ")


def test_stooq_provider_intraday_not_implemented():
    provider = StooqProvider()
    with pytest.raises(NotImplementedError):
        provider.get_ohlcv("AAPL", interval="15m")


class FailingProvider:
    name = "failing"

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        raise DataProviderError("boom")

    def get_quote(self, symbol):
        raise DataProviderError("boom")


class NotImplementedProvider:
    name = "not_implemented"

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        raise NotImplementedError

    def get_quote(self, symbol):
        raise NotImplementedError


class WorkingProvider:
    name = "working"

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        return pd.DataFrame({"open": [1], "high": [2], "low": [0], "close": [1.5], "volume": [100]})

    def get_quote(self, symbol):
        return QuoteData(symbol=symbol, price=1.5, change_pct_24h=0.1, volume=100, avg_volume_20d=90)


def test_composite_falls_through_to_working_provider():
    composite = CompositeDataProvider([FailingProvider(), NotImplementedProvider(), WorkingProvider()])
    quote = composite.get_quote("AAPL")
    assert quote.price == 1.5


def test_composite_raises_when_all_providers_fail():
    composite = CompositeDataProvider([FailingProvider(), NotImplementedProvider()])
    with pytest.raises(AllProvidersFailedError):
        composite.get_quote("AAPL")


def test_composite_earnings_date_returns_none_instead_of_raising():
    class NoEarnings:
        name = "no_earnings"

        def get_earnings_date(self, symbol):
            raise NotImplementedError

    composite = CompositeDataProvider([NoEarnings()])
    assert composite.get_earnings_date("AAPL") is None


def test_composite_news_falls_back_to_google_when_yahoo_answers_empty(monkeypatch):
    from app.data_providers.base import NewsItem
    from app.services import company_news

    class EmptyNews:
        name = "empty"

        def get_news(self, symbol, limit=5):
            return []

        def get_company_overview(self, symbol):
            raise DataProviderError("no overview")

    item = NewsItem(headline="Vistra wins $4.2 billion DOE loan", source="x", url="u", published_at="2026-10-17T00:00:00Z")
    monkeypatch.setenv("GOOGLE_NEWS_ENABLED", "true")
    monkeypatch.setattr(company_news._google, "search", lambda query, limit=5, days=7: [item])
    assert CompositeDataProvider([EmptyNews()]).get_news("VST") == [item]


def test_yfinance_news_uses_search_when_ticker_news_is_empty(monkeypatch):
    from app.data_providers import yfinance_provider as yp

    class FakeTicker:
        def __init__(self, symbol):
            self.news = []

    class FakeSearch:
        def __init__(self, *a, **k):
            self.news = [
                {"title": "About VST", "publisher": "p", "link": "u", "providerPublishTime": 1792339170, "relatedTickers": ["vst"]},
                {"title": "About others", "publisher": "p", "link": "u", "providerPublishTime": 1792339170, "relatedTickers": ["AAPL"]},
            ]

    monkeypatch.setattr(yp.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(yp.yf, "Search", FakeSearch)
    assert [n.headline for n in yp.YFinanceProvider().get_news("VST")] == ["About VST"]
