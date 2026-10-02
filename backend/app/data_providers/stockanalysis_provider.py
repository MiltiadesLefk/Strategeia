from __future__ import annotations

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

BASE_URL = "https://api.stockanalysis.com/api/symbol"
# Same as the other daily providers: this feeds the exit engine as a fallback,
# so it must not serve a staler bar than the primary does.
OHLCV_TTL = 15 * 60

_PERIOD_TO_DAYS = {"5d": 10, "1mo": 31, "3mo": 93, "6mo": 186, "1y": 366, "2y": 732, "5y": 1830}
# The site's own range keys; a longer window than asked is trimmed after.
_PERIOD_TO_RANGE = {"5d": "1Y", "1mo": "1Y", "3mo": "1Y", "6mo": "1Y", "1y": "1Y", "2y": "5Y", "5y": "5Y"}
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Strategeia)", "Accept": "application/json"}


class StockAnalysisProvider:
    """Daily OHLCV only, no key. Unofficial endpoint of stockanalysis.com (an
    independent operator from Yahoo and Nasdaq); it may change without notice.
    Bars are the unadjusted open/high/low/close, like Nasdaq's. Stocks and ETFs
    only: crypto and index symbols raise NotImplementedError so the chain moves on."""

    name = "stockanalysis"

    def _fetch(self, kind: str, symbol: str, rng: str) -> list[dict]:
        try:
            resp = httpx.get(
                f"{BASE_URL}/{kind}/{symbol.lower()}/history",
                params={"type": "full", "range": rng},
                headers=_HEADERS,
                timeout=10,
            )
        except httpx.HTTPError as exc:
            raise DataProviderError(f"stockanalysis get_ohlcv({symbol}) failed: {exc}") from exc
        if resp.status_code == 404:
            return []
        try:
            resp.raise_for_status()
            rows = resp.json().get("data")
        except (httpx.HTTPError, ValueError) as exc:
            raise DataProviderError(f"stockanalysis get_ohlcv({symbol}) failed: {exc}") from exc
        return rows if isinstance(rows, list) else []

    @cached(OHLCV_TTL)
    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        if interval != "1d":
            raise NotImplementedError("stockanalysis provider only supports daily bars")
        if symbol.startswith("^") or "-" in symbol or "=" in symbol:
            raise NotImplementedError("stockanalysis provider covers stocks and ETFs only")
        rng = _PERIOD_TO_RANGE.get(period)
        if rng is None:
            raise NotImplementedError(f"stockanalysis has no mapping for period={period!r}")

        rows = self._fetch("s", symbol, rng) or self._fetch("e", symbol, rng)
        if not rows:
            raise DataProviderError(f"stockanalysis returned no data for {symbol}")

        records = []
        for r in rows:
            try:
                records.append(
                    {
                        "date": pd.to_datetime(r["t"]),
                        "open": float(r["o"]),
                        "high": float(r["h"]),
                        "low": float(r["l"]),
                        "close": float(r["c"]),
                        "volume": float(r.get("v") or 0.0),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue  # a half-formed bar is worse than a missing one
        df = pd.DataFrame(records)
        if df.empty:
            raise DataProviderError(f"stockanalysis returned unparseable data for {symbol}")
        cutoff = date.today() - timedelta(days=_PERIOD_TO_DAYS[period])
        df = df[df["date"].dt.date >= cutoff].sort_values("date").reset_index(drop=True)
        if df.empty:
            raise DataProviderError(f"stockanalysis had no data for {symbol} in the requested period")
        return df[["date", "open", "high", "low", "close", "volume"]]

    def get_quote(self, symbol: str) -> QuoteData:
        raise NotImplementedError("stockanalysis provider does not support quotes")

    def get_company_overview(self, symbol: str) -> CompanyOverview:
        raise NotImplementedError("stockanalysis provider does not support company overviews")

    def get_financials(self, symbol: str) -> FinancialsData:
        raise NotImplementedError("stockanalysis provider does not support financials")

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        raise NotImplementedError("stockanalysis provider does not support news")

    def get_earnings_date(self, symbol: str) -> date | None:
        raise NotImplementedError("stockanalysis provider does not support earnings dates")

    def get_earnings_estimate(self, symbol: str) -> EarningsEstimate | None:
        raise NotImplementedError("stockanalysis provider does not support earnings estimates")

    def get_options_summary(self, symbol: str) -> OptionsSummary:
        raise NotImplementedError("stockanalysis provider does not support options data")

    def get_options_chain(self, symbol: str, expiration: str | None = None):
        raise NotImplementedError("stockanalysis provider does not support options data")

    def get_insider_activity(self, symbol: str):
        raise NotImplementedError("stockanalysis provider does not implement get_insider_activity")

    def get_earnings_history(self, symbol: str, limit: int = 12):
        raise NotImplementedError("stockanalysis provider does not implement get_earnings_history")
