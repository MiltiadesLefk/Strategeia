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
    change: float | None = None
    change_pct: float | None = None
    market_cap: float | None
    pe_ratio: float | None
    revenue_ttm: float | None
    eps_ttm: float | None
    revenue_yoy_pct: float | None = None
    week52_low: float | None
    week52_high: float | None
    financials: list[FinancialYearSchema]
    news: list[NewsItemSchema]
    catalysts: list[str]
    earnings_date: str | None
    earnings_days_until: int | None = None
    earnings_fiscal_label: str | None = None
    earnings_eps_estimate: float | None = None
    earnings_revenue_estimate: float | None = None
    ai_summary: str
    ai_provider: str
    ai_error: str | None = None
    generated_at: str
