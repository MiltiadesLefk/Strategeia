from __future__ import annotations

from datetime import date

from sqlmodel import Session

from app.analysis.insight_text import research_summary_text
from app.analysis.trend import analyze_chart
from app.data_providers.base import AllProvidersFailedError, DataProvider
from app.llm_providers.base import LLMProvider
from app.llm_providers.factory import generate_with_fallback
from app.llm_providers.prompts import build_research_summary_prompt
from app.schemas.research_schemas import FinancialYearSchema, NewsItemSchema, ResearchResponse
from app.services.archive_service import archive_fetched_data
from app.timeutil import utcnow_naive

EARNINGS_SOON_DAYS = 45
NEAR_52W_HIGH_PCT = 0.05


def _revenue_yoy_pct(financial_years) -> float | None:
    if len(financial_years) < 2:
        return None
    latest, prior = financial_years[-1], financial_years[-2]
    if not prior.revenue:
        return None
    return (latest.revenue - prior.revenue) / prior.revenue * 100


def _price_change(price: float, change_pct: float | None) -> float | None:
    """Derives the dollar change implied by a known percent change, relative
    to the same price shown in the header — avoids a mismatch between a
    quote-endpoint price and the OHLCV-derived `chart.price` used elsewhere."""
    if change_pct is None:
        return None
    prev_close = price / (1 + change_pct / 100)
    return price - prev_close


def _derive_catalysts(overview, financials_years, earnings_date, chart) -> list[str]:
    catalysts: list[str] = []
    if earnings_date and (earnings_date - date.today()).days <= EARNINGS_SOON_DAYS:
        catalysts.append(f"Earnings scheduled for {earnings_date.isoformat()}")
    if overview.week52_high and chart.price >= overview.week52_high * (1 - NEAR_52W_HIGH_PCT):
        catalysts.append("Trading within 5% of its 52-week high")
    if len(financials_years) >= 2:
        latest, prior = financials_years[-1], financials_years[-2]
        if prior.revenue and latest.revenue > prior.revenue:
            growth_pct = (latest.revenue - prior.revenue) / prior.revenue * 100
            catalysts.append(f"Revenue grew {growth_pct:.1f}% year over year")
    if chart.momentum == "Strong":
        catalysts.append(f"{chart.trend} trend showing strong momentum")
    return catalysts


def get_research(
    symbol: str,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    session: Session | None = None,
) -> ResearchResponse:
    """`session` is optional and only used to save the news and fundamentals
    just fetched into the dated archive (best-effort; see archive_service).
    Without one, nothing is saved and the page works exactly the same."""
    overview = data_provider.get_company_overview(symbol)
    ohlcv = data_provider.get_ohlcv(symbol, period="1y", interval="1d")
    chart = analyze_chart(ohlcv)

    try:
        financials = data_provider.get_financials(symbol)
        financial_years = financials.years
    except AllProvidersFailedError:
        financial_years = []

    try:
        news = data_provider.get_news(symbol, limit=5)
    except AllProvidersFailedError:
        news = []

    archive_fetched_data(
        session, symbol, overview=overview, financial_years=financial_years, news=news, data_provider=data_provider
    )

    earnings_date = data_provider.get_earnings_date(symbol)
    has_upcoming_earnings = bool(earnings_date and (earnings_date - date.today()).days <= EARNINGS_SOON_DAYS)
    catalysts = _derive_catalysts(overview, financial_years, earnings_date, chart)
    earnings_estimate = data_provider.get_earnings_estimate(symbol)

    try:
        change_pct = data_provider.get_quote(symbol).change_pct_24h
    except AllProvidersFailedError:
        change_pct = None

    fallback_text = research_summary_text(symbol, chart, has_upcoming_earnings)
    prompt = build_research_summary_prompt(symbol, chart, has_upcoming_earnings, catalysts)
    llm_result = generate_with_fallback(llm_provider, prompt, fallback_text)

    return ResearchResponse(
        symbol=symbol,
        name=overview.name,
        price=chart.price,
        change=_price_change(chart.price, change_pct),
        change_pct=change_pct,
        market_cap=overview.market_cap,
        pe_ratio=overview.pe_ratio,
        revenue_ttm=overview.revenue_ttm,
        eps_ttm=overview.eps_ttm,
        revenue_yoy_pct=_revenue_yoy_pct(financial_years),
        week52_low=overview.week52_low,
        week52_high=overview.week52_high,
        financials=[FinancialYearSchema(year=fy.year, revenue=fy.revenue, net_income=fy.net_income) for fy in financial_years],
        news=[NewsItemSchema(headline=n.headline, source=n.source, url=n.url, published_at=n.published_at) for n in news],
        catalysts=catalysts,
        earnings_date=earnings_date.isoformat() if earnings_date else None,
        earnings_days_until=(earnings_date - date.today()).days if earnings_date else None,
        earnings_fiscal_label=earnings_estimate.fiscal_period_label if earnings_estimate else None,
        earnings_eps_estimate=earnings_estimate.eps_estimate if earnings_estimate else None,
        earnings_revenue_estimate=earnings_estimate.revenue_estimate if earnings_estimate else None,
        ai_summary=llm_result.text,
        ai_provider=llm_result.provider,
        ai_error=llm_result.error,
        generated_at=utcnow_naive().isoformat() + "Z",
    )
