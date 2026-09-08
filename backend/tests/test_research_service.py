from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.data_providers.base import (
    AllProvidersFailedError,
    CompanyOverview,
    EarningsEstimate,
    FinancialsData,
    FinancialYear,
    NewsItem,
    QuoteData,
)
from app.data_providers.composite_provider import CompositeDataProvider
from app.llm_providers.null_provider import NullLLMProvider
from app.services import research_service
from app.services.research_service import _price_change, _revenue_yoy_pct


class FakeResearchProvider:
    name = "fake"

    def __init__(self, change_pct_24h: float | None = 2.5, earnings_in_days: int | None = 10):
        self._change_pct_24h = change_pct_24h
        self._earnings_in_days = earnings_in_days

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        n = 150
        closes = 100 + np.arange(n) * 0.3
        return pd.DataFrame(
            {
                "open": closes - 0.3,
                "high": closes + 1.0,
                "low": closes - 1.0,
                "close": closes,
                "volume": [1_000_000.0] * n,
            }
        )

    def get_quote(self, symbol):
        return QuoteData(symbol=symbol, price=140.0, change_pct_24h=self._change_pct_24h, volume=1_000_000, avg_volume_20d=900_000)

    def get_company_overview(self, symbol):
        return CompanyOverview(
            symbol=symbol,
            name="Fake Corp",
            market_cap=1_000_000_000.0,
            pe_ratio=25.0,
            revenue_ttm=400_000_000_000.0,
            eps_ttm=6.0,
            week52_low=100.0,
            week52_high=150.0,
        )

    def get_financials(self, symbol):
        return FinancialsData(
            symbol=symbol,
            years=[
                FinancialYear(year=2023, revenue=380_000.0, net_income=90_000.0),
                FinancialYear(year=2024, revenue=400_000.0, net_income=95_000.0),
            ],
        )

    def get_news(self, symbol, limit=5):
        return [NewsItem(headline="Fake news", source="Wire", url="https://example.com/a", published_at="2026-09-01T00:00:00")]

    def get_earnings_date(self, symbol):
        if self._earnings_in_days is None:
            return None
        return date.today() + timedelta(days=self._earnings_in_days)

    def get_earnings_estimate(self, symbol):
        if self._earnings_in_days is None:
            return None
        return EarningsEstimate(
            date=date.today() + timedelta(days=self._earnings_in_days),
            fiscal_period_label="Q4 2026",
            eps_estimate=1.47,
            revenue_estimate=101_800_000_000.0,
        )


def test_revenue_yoy_pct_computes_growth():
    years = [FinancialYear(year=2023, revenue=380_000.0, net_income=90_000.0), FinancialYear(year=2024, revenue=400_000.0, net_income=95_000.0)]
    assert _revenue_yoy_pct(years) == pytest.approx((400_000.0 - 380_000.0) / 380_000.0 * 100)


def test_revenue_yoy_pct_none_with_fewer_than_two_years():
    assert _revenue_yoy_pct([FinancialYear(year=2024, revenue=100.0, net_income=10.0)]) is None


def test_price_change_derives_dollar_change_from_percent():
    change = _price_change(101.0, 1.0)
    assert change == pytest.approx(101.0 - 101.0 / 1.01)


def test_price_change_none_when_no_percent_available():
    assert _price_change(101.0, None) is None


def test_get_research_populates_new_fields():
    response = research_service.get_research("AAPL", FakeResearchProvider(), NullLLMProvider())

    assert response.change_pct == pytest.approx(2.5)
    # Internally consistent with the % change actually returned, regardless
    # of the exact chart price analyze_chart derives from the OHLCV fixture.
    assert response.change == pytest.approx(response.price - response.price / (1 + response.change_pct / 100))
    assert response.revenue_yoy_pct == pytest.approx((400_000.0 - 380_000.0) / 380_000.0 * 100)
    assert response.earnings_days_until == 10
    assert response.earnings_fiscal_label == "Q4 2026"
    assert response.earnings_eps_estimate == pytest.approx(1.47)
    assert response.earnings_revenue_estimate == pytest.approx(101_800_000_000.0)
    assert response.generated_at.endswith("Z")


def test_get_research_handles_no_upcoming_earnings():
    response = research_service.get_research("AAPL", FakeResearchProvider(earnings_in_days=None), NullLLMProvider())

    assert response.earnings_date is None
    assert response.earnings_days_until is None
    assert response.earnings_eps_estimate is None


def test_get_research_survives_quote_failure():
    class NoQuoteProvider(FakeResearchProvider):
        def get_quote(self, symbol):
            raise AllProvidersFailedError("no quote")

    response = research_service.get_research("AAPL", NoQuoteProvider(), NullLLMProvider())

    assert response.change is None
    assert response.change_pct is None


def test_composite_earnings_estimate_returns_none_instead_of_raising():
    class NoEstimate:
        name = "no_estimate"

        def get_earnings_estimate(self, symbol):
            raise NotImplementedError

    composite = CompositeDataProvider([NoEstimate()])
    assert composite.get_earnings_estimate("AAPL") is None
