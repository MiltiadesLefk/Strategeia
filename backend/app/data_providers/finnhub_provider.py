from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx
import pandas as pd

from app.data_providers.base import (
    CompanyOverview,
    DataProviderError,
    EarningsEstimate,
    FinancialsData,
    NewsItem,
    OptionsSummary,
    QuoteData,
)
from app.data_providers.cache import cached
from app.timeutil import utc_from_timestamp_naive

BASE_URL = "https://finnhub.io/api/v1"
QUOTE_TTL = 15 * 60
OVERVIEW_TTL = 24 * 60 * 60
NEWS_TTL = 30 * 60
EARNINGS_TTL = 24 * 60 * 60


class FinnhubProvider:
    """Optional, free-tier keyed provider (personal/non-commercial use only).

    Free tier does not reliably expose full income-statement history or
    intraday candles, so get_ohlcv/get_financials are not implemented here —
    the composite provider falls through to yfinance/stooq for those.
    """

    name = "finnhub"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("FinnhubProvider requires a non-empty api_key")
        self._api_key = api_key

    def _get(self, path: str, params: dict) -> dict:
        try:
            resp = httpx.get(
                f"{BASE_URL}{path}", params={**params, "token": self._api_key}, timeout=10
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DataProviderError(f"finnhub {path} failed: {exc}") from exc
        return resp.json()

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        raise NotImplementedError("finnhub free tier does not support historical candles")

    @cached(QUOTE_TTL)
    def get_quote(self, symbol: str) -> QuoteData:
        data = self._get("/quote", {"symbol": symbol})
        if not data or data.get("c") in (None, 0):
            raise DataProviderError(f"finnhub returned no quote for {symbol}")
        metric = self._get("/stock/metric", {"symbol": symbol, "metric": "price"}).get("metric", {})
        avg_volume = metric.get("10DayAverageTradingVolume", 0.0) or 0.0
        return QuoteData(
            symbol=symbol,
            price=float(data["c"]),
            change_pct_24h=float(data.get("dp") or 0.0),
            volume=0.0,
            avg_volume_20d=float(avg_volume) * 1_000_000 if avg_volume else 0.0,
        )

    @cached(OVERVIEW_TTL)
    def get_company_overview(self, symbol: str) -> CompanyOverview:
        profile = self._get("/stock/profile2", {"symbol": symbol})
        if not profile:
            raise DataProviderError(f"finnhub returned no profile for {symbol}")
        metric = self._get("/stock/metric", {"symbol": symbol, "metric": "all"}).get("metric", {})
        return CompanyOverview(
            symbol=symbol,
            name=profile.get("name", symbol),
            market_cap=(profile.get("marketCapitalization") or 0) * 1_000_000 or None,
            pe_ratio=metric.get("peBasicExclExtraTTM") or metric.get("peExclExtraTTM"),
            revenue_ttm=None,
            eps_ttm=metric.get("epsInclExtraItemsTTM") or metric.get("epsTTM"),
            week52_low=metric.get("52WeekLow"),
            week52_high=metric.get("52WeekHigh"),
        )

    def get_financials(self, symbol: str) -> FinancialsData:
        raise NotImplementedError("finnhub free tier does not reliably expose full financial statement history")

    @cached(NEWS_TTL)
    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        today = date.today()
        data = self._get(
            "/company-news",
            {"symbol": symbol, "from": (today - timedelta(days=14)).isoformat(), "to": today.isoformat()},
        )
        items: list[NewsItem] = []
        for entry in (data or [])[:limit]:
            published_at = ""
            if entry.get("datetime"):
                published_at = utc_from_timestamp_naive(entry["datetime"]).isoformat()
            items.append(
                NewsItem(
                    headline=entry.get("headline", ""),
                    source=entry.get("source", "Unknown"),
                    url=entry.get("url", ""),
                    published_at=published_at,
                )
            )
        return items

    @cached(EARNINGS_TTL)
    def get_earnings_date(self, symbol: str) -> date | None:
        today = date.today()
        data = self._get(
            "/calendar/earnings",
            {"symbol": symbol, "from": today.isoformat(), "to": (today + timedelta(days=180)).isoformat()},
        )
        entries = (data or {}).get("earningsCalendar", [])
        if not entries:
            return None
        try:
            return min(datetime.strptime(e["date"], "%Y-%m-%d").date() for e in entries if e.get("date"))
        except (KeyError, ValueError):
            return None

    @cached(EARNINGS_TTL)
    def get_earnings_estimate(self, symbol: str) -> EarningsEstimate | None:
        today = date.today()
        data = self._get(
            "/calendar/earnings",
            {"symbol": symbol, "from": today.isoformat(), "to": (today + timedelta(days=180)).isoformat()},
        )
        upcoming: list[tuple[date, dict]] = []
        for entry in (data or {}).get("earningsCalendar", []):
            if not entry.get("date"):
                continue
            try:
                entry_date = datetime.strptime(entry["date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            if entry_date >= today:
                upcoming.append((entry_date, entry))
        if not upcoming:
            return None
        next_date, entry = min(upcoming, key=lambda pair: pair[0])
        quarter, year = entry.get("quarter"), entry.get("year")
        return EarningsEstimate(
            date=next_date,
            fiscal_period_label=f"Q{quarter} {year}" if quarter and year else None,
            eps_estimate=entry.get("epsEstimate"),
            revenue_estimate=entry.get("revenueEstimate"),
        )

    def get_options_summary(self, symbol: str) -> OptionsSummary:
        raise NotImplementedError("finnhub free tier does not expose options chain data")
