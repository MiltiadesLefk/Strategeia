from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

import pandas as pd

from app.data_providers.base import (
    CompanyOverview,
    DataProviderError,
    FinancialsData,
    NewsItem,
    OptionsSummary,
    QuoteData,
)
from app.data_providers.cache import cached

OHLCV_TTL = 15 * 60  # matches YFinanceProvider — see the rationale there
QUOTE_TTL = 15 * 60
OVERVIEW_TTL = 24 * 60 * 60

BASE_URL = "https://api.nasdaq.com/api"
REQUEST_TIMEOUT = 20

# api.nasdaq.com is the endpoint nasdaq.com's own site calls. It needs no key,
# but it does check that the request looks like it came from that site — drop
# the Origin/Referer and it returns 403. Same public-endpoint arrangement as
# yfinance (see CLAUDE.md): unofficial, unlicensed, and liable to change.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}

# Nasdaq's chart endpoint takes a date range, not a period string. Mapped in
# calendar days (deliberately generous — trading days are ~69% of calendar
# days, and over-fetching costs nothing while under-fetching silently
# shortens EMA50's runway).
PERIOD_DAYS = {
    "5d": 10,
    "1mo": 45,
    "3mo": 130,
    "6mo": 250,
    "1y": 500,
    "2y": 900,
    "5y": 2000,
    "max": 7300,
}

# Assets Nasdaq classes separately; getting this wrong returns an empty payload
# rather than an error, so it's derived from the symbol shape.
_CRYPTO_SUFFIX = "-USD"


def _asset_class(symbol: str) -> str:
    if symbol.upper().endswith(_CRYPTO_SUFFIX):
        return "crypto"
    if symbol.startswith("^"):
        return "index"
    return "stocks"


def _to_float(raw) -> float | None:
    """Nasdaq returns display strings, not numbers: "$332.27", "50,716,997",
    "+1.75%", "N/A". Everything that isn't a real number becomes None rather
    than 0.0 — a missing field must not read as a real zero."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text or text.upper() in {"N/A", "NA", "--", "UNCH"}:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", text.replace("(", "-").replace(")", ""))
    if cleaned in {"", "-", ".", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _summary_value(summary: dict, key: str):
    entry = summary.get(key)
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _split_range(raw) -> tuple[float | None, float | None]:
    """"$344.5699/$226.65" -> (344.5699, 226.65). Nasdaq orders these
    high-then-low."""
    if not raw or "/" not in str(raw):
        return None, None
    high, _, low = str(raw).partition("/")
    return _to_float(high), _to_float(low)


class NasdaqProvider:
    """Nasdaq's public quote/chart API.

    Positioned as the FIRST fallback rather than the primary (see
    data_providers/factory.py). Measured on this machine: Nasdaq takes
    1.2-3.3s for a quote and ~1.5s for a year of candles, against yfinance's
    ~0.9s and ~0.14s. Across an auto-scan pass — roughly ten calls per symbol,
    fifty symbols — that difference is minutes per scan, so yfinance keeps the
    hot path. What Nasdaq buys is a far better *degraded* mode: before it,
    a Yahoo outage dropped the app to Stooq, which serves OHLCV only, so
    quotes and fundamentals simply vanished. Nasdaq covers all three.

    Implements the methods it genuinely serves and raises NotImplementedError
    for the rest, which CompositeDataProvider treats as "try the next one".
    """

    name = "nasdaq"

    def _get(self, path: str, params: dict[str, str]) -> dict:
        query = urllib.parse.urlencode(params)
        url = f"{BASE_URL}/{path}?{query}"
        request = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise DataProviderError(f"nasdaq {path} failed: {exc}") from exc

        # Nasdaq signals "no such symbol" in the body with HTTP 200.
        status = (payload.get("status") or {}).get("rCode")
        if status is not None and int(status) != 200:
            messages = (payload.get("status") or {}).get("bCodeMessage") or []
            detail = "; ".join(m.get("errorMessage", "") for m in messages if isinstance(m, dict))
            raise DataProviderError(f"nasdaq {path} returned {status}: {detail or 'no detail'}")
        if payload.get("data") is None:
            raise DataProviderError(f"nasdaq {path} returned no data")
        return payload["data"]

    @cached(OHLCV_TTL)
    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        if interval != "1d":
            # Nasdaq's chart endpoint is daily-only. Intraday (the Analysis
            # page's 1D/1W ranges) and weekly stay with yfinance.
            raise NotImplementedError("nasdaq provides daily bars only")

        days = PERIOD_DAYS.get(period)
        if days is None:
            raise NotImplementedError(f"nasdaq has no mapping for period={period!r}")

        to_date = date.today()
        from_date = to_date - timedelta(days=days)
        data = self._get(
            f"quote/{symbol.upper()}/chart",
            {
                "assetclass": _asset_class(symbol),
                "fromdate": from_date.isoformat(),
                "todate": to_date.isoformat(),
            },
        )

        rows = []
        for point in data.get("chart") or []:
            bar = point.get("z") or {}
            parsed = {
                "open": _to_float(bar.get("open")),
                "high": _to_float(bar.get("high")),
                "low": _to_float(bar.get("low")),
                "close": _to_float(bar.get("close")),
                "volume": _to_float(bar.get("volume")) or 0.0,
            }
            if any(parsed[k] is None for k in ("open", "high", "low", "close")):
                continue  # a half-formed bar is worse than a missing one
            try:
                parsed["date"] = datetime.strptime(bar.get("dateTime", ""), "%m/%d/%Y")
            except ValueError:
                continue
            rows.append(parsed)

        if not rows:
            raise DataProviderError(f"nasdaq returned no usable OHLCV rows for {symbol}")

        frame = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        return frame[["date", "open", "high", "low", "close", "volume"]]

    @cached(QUOTE_TTL)
    def get_quote(self, symbol: str) -> QuoteData:
        upper = symbol.upper()
        asset_class = _asset_class(upper)
        info = self._get(f"quote/{upper}/info", {"assetclass": asset_class})
        primary = info.get("primaryData") or {}

        price = _to_float(primary.get("lastSalePrice"))
        if price is None:
            raise DataProviderError(f"nasdaq returned no last price for {symbol}")

        volume = _to_float(primary.get("volume")) or 0.0
        change_pct = _to_float(primary.get("percentageChange")) or 0.0
        # percentageChange arrives as "+1.75%" / "-0.03%"; _to_float keeps the
        # magnitude but the leading sign is stripped along with the '+'.
        if str(primary.get("percentageChange", "")).strip().startswith("-"):
            change_pct = -abs(change_pct)

        avg_volume = 0.0
        try:
            summary = self._get(f"quote/{upper}/summary", {"assetclass": asset_class})
            avg_volume = _to_float(_summary_value(summary.get("summaryData") or {}, "AverageVolume")) or 0.0
        except DataProviderError:
            pass  # volume_ratio degrades to 1.0 upstream; not worth failing the quote over

        return QuoteData(
            symbol=upper,
            price=price,
            change_pct_24h=change_pct,
            volume=volume,
            avg_volume_20d=avg_volume,
        )

    @cached(OVERVIEW_TTL)
    def get_company_overview(self, symbol: str) -> CompanyOverview:
        upper = symbol.upper()
        asset_class = _asset_class(upper)
        data = self._get(f"quote/{upper}/summary", {"assetclass": asset_class})
        summary = data.get("summaryData") or {}
        if not summary:
            raise DataProviderError(f"nasdaq returned no summary for {symbol}")

        week52_high, week52_low = _split_range(_summary_value(summary, "FiftTwoWeekHighLow"))

        name = upper
        try:
            info = self._get(f"quote/{upper}/info", {"assetclass": asset_class})
            name = info.get("companyName") or upper
        except DataProviderError:
            pass

        return CompanyOverview(
            symbol=upper,
            name=name,
            market_cap=_to_float(_summary_value(summary, "MarketCap")),
            pe_ratio=_to_float(_summary_value(summary, "PERatio")),
            revenue_ttm=None,  # not exposed by this endpoint
            eps_ttm=_to_float(_summary_value(summary, "EarningsPerShare")),
            week52_low=week52_low,
            week52_high=week52_high,
        )

    # --- not served by this provider; composite falls through to the next ---

    def get_financials(self, symbol: str) -> FinancialsData:
        raise NotImplementedError("nasdaq provider does not implement get_financials")

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        raise NotImplementedError("nasdaq provider does not implement get_news")

    def get_earnings_date(self, symbol: str) -> date | None:
        # The earnings-surprise endpoint reports quarters already *reported*,
        # not the next scheduled date, so it cannot answer this question.
        raise NotImplementedError("nasdaq provider does not implement get_earnings_date")

    def get_earnings_estimate(self, symbol: str):
        raise NotImplementedError("nasdaq provider does not implement get_earnings_estimate")

    def get_options_summary(self, symbol: str) -> OptionsSummary:
        raise NotImplementedError("nasdaq provider does not implement get_options_summary")

    def get_insider_activity(self, symbol: str):
        raise NotImplementedError("nasdaq provider does not implement get_insider_activity")
