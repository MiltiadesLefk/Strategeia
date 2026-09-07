from __future__ import annotations

from pydantic import BaseModel


class FinancialYearSchema(BaseModel):
    year: int
    revenue: float
    net_income: float


class NewsItemSchema(BaseModel):
    headline: str
    source: str
    url: str
    published_at: str


class ResearchResponse(BaseModel):
    symbol: str
    name: str
    price: float
    market_cap: float | None
    pe_ratio: float | None
    revenue_ttm: float | None
    eps_ttm: float | None
    week52_low: float | None
    week52_high: float | None
    financials: list[FinancialYearSchema]
    news: list[NewsItemSchema]
    catalysts: list[str]
    earnings_date: str | None
    ai_summary: str
    ai_provider: str
    ai_error: str | None = None
