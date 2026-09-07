from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd


class DataProviderError(Exception):
    """A single provider failed for a single call; composite should try the next one."""


class AllProvidersFailedError(Exception):
    """Every configured provider failed for this call. Never fabricate data on this."""


@dataclass
class QuoteData:
    symbol: str
    price: float
    change_pct_24h: float
    volume: float
    avg_volume_20d: float


@dataclass
class CompanyOverview:
    symbol: str
    name: str
    market_cap: float | None
    pe_ratio: float | None
    revenue_ttm: float | None
    eps_ttm: float | None
    week52_low: float | None
    week52_high: float | None


@dataclass
class FinancialYear:
    year: int
    revenue: float
    net_income: float


@dataclass
class FinancialsData:
    symbol: str
    years: list[FinancialYear]


@dataclass
class NewsItem:
    headline: str
    source: str
    url: str
    published_at: str


class DataProvider(Protocol):
    name: str

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame: ...

    def get_quote(self, symbol: str) -> QuoteData: ...

    def get_company_overview(self, symbol: str) -> CompanyOverview: ...

    def get_financials(self, symbol: str) -> FinancialsData: ...

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]: ...

    def get_earnings_date(self, symbol: str) -> date | None: ...
