from __future__ import annotations

import io
from datetime import date, timedelta

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

STOOQ_URL = "https://stooq.com/q/d/l/"
OHLCV_TTL = 24 * 60 * 60

_PERIOD_TO_DAYS = {
    "1mo": 31,
    "3mo": 93,
    "6mo": 186,
    "1y": 366,
    "2y": 732,
    "5y": 1830,
}


class StooqProvider:
    """OHLCV-only fallback, no API key required. Daily bars only."""

    name = "stooq"

    @cached(OHLCV_TTL)
    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        if interval != "1d":
            raise NotImplementedError("stooq provider only supports daily bars")
        try:
            resp = httpx.get(STOOQ_URL, params={"s": f"{symbol.lower()}.us", "i": "d"}, timeout=10)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DataProviderError(f"stooq get_ohlcv({symbol}) failed: {exc}") from exc

        text = resp.text
        if not text or text.startswith("No data") or "Exceeded the daily hits limit" in text:
            raise DataProviderError(f"stooq returned no data for {symbol}")

        df = pd.read_csv(io.StringIO(text))
        if df.empty or "Date" not in df.columns:
            raise DataProviderError(f"stooq returned unparseable data for {symbol}")

        df = df.rename(
            columns={
                "Date": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        df["date"] = pd.to_datetime(df["date"])
        cutoff = date.today() - timedelta(days=_PERIOD_TO_DAYS.get(period, 186))
        df = df[df["date"].dt.date >= cutoff].reset_index(drop=True)
        if df.empty:
            raise DataProviderError(f"stooq had no data for {symbol} in the requested period")
        return df[["date", "open", "high", "low", "close", "volume"]]

    def get_quote(self, symbol: str) -> QuoteData:
        raise NotImplementedError("stooq provider does not support quotes")

    def get_company_overview(self, symbol: str) -> CompanyOverview:
        raise NotImplementedError("stooq provider does not support company overviews")

    def get_financials(self, symbol: str) -> FinancialsData:
        raise NotImplementedError("stooq provider does not support financials")

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        raise NotImplementedError("stooq provider does not support news")

    def get_earnings_date(self, symbol: str) -> date | None:
        raise NotImplementedError("stooq provider does not support earnings dates")

    def get_earnings_estimate(self, symbol: str) -> EarningsEstimate | None:
        raise NotImplementedError("stooq provider does not support earnings estimates")

    def get_options_summary(self, symbol: str) -> OptionsSummary:
        raise NotImplementedError("stooq provider does not support options data")
