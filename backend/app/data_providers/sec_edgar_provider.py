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
    InsiderActivity,
    NewsItem,
    OptionsSummary,
    QuoteData,
)
from app.config import get_infra_settings
from app.data_providers.cache import cached

# Insider filings move slowly and EDGAR is authoritative, so these can be long.
TICKER_MAP_TTL = 7 * 24 * 60 * 60
INSIDER_TTL = 12 * 60 * 60

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

# EDGAR's fair-access policy requires a descriptive User-Agent identifying the
# requester, and caps traffic at 10 requests/second. Unlike the other
# providers here this is an official, documented, licensed-for-use API — not a
# scraped endpoint — so it is worth staying inside the rules.
REQUEST_TIMEOUT = 20


def _headers() -> dict[str, str]:
    """EDGAR requires a User-Agent identifying the requester with a contact
    address, and rejects any that embeds a URL — a UA containing "github.com"
    returns 403 where the same request with an email-shaped contact returns
    200. Configurable via SEC_EDGAR_USER_AGENT (see InfraSettings)."""
    return {
        "User-Agent": get_infra_settings().sec_edgar_user_agent,
        "Accept": "application/json, text/xml, */*",
    }

# How far back a Form 4 still counts, and how many filings to actually open.
# Each filing is its own HTTP request, so this bounds the cost of evaluating a
# symbol: a busy large-cap files hundreds of Form 4s a year and reading all of
# them would dominate a scan for a signal that is capped at one point.
INSIDER_WINDOW_DAYS = 90
MAX_FILINGS_TO_READ = 12

# Form 4 transaction codes. Only open-market trades are counted: an option
# exercise (M), a grant (A) or a tax withholding (F) is compensation
# machinery, not a decision to take a position, and treating them as buys
# would show constant "insider buying" at every company that pays in equity.
OPEN_MARKET_BUY = "P"
OPEN_MARKET_SELL = "S"

_TRANSACTION_CODE_RE = re.compile(r"<transactionCode>\s*(\w)\s*</transactionCode>")
_SHARES_RE = re.compile(r"<transactionShares>\s*<value>\s*([\d.]+)\s*</value>")
_PRICE_RE = re.compile(r"<transactionPricePerShare>\s*<value>\s*([\d.]*)\s*</value>")
# EDGAR's primaryDocument for a Form 4 points at the XSL-rendered HTML view
# (".../xslF345X06/form4.xml"), which contains none of the tags above. The raw
# XML sits at the same path with that directory removed.
_XSL_PREFIX_RE = re.compile(r"^xsl[^/]*/")


class SecEdgarProvider:
    """SEC EDGAR — insider (Form 4) transactions only.

    This is the one genuinely *predictive* free dataset available here, and it
    fits the scoring layer's shape exactly: a named, capped, direction-aware
    dimension (see analysis/insider_scoring.py). It deliberately implements
    nothing else — EDGAR is not a quote source, and pretending otherwise would
    put a slow filing API in the hot path of every scan.
    """

    name = "sec_edgar"

    def _fetch(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers=_headers())
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DataProviderError(f"sec_edgar fetch failed for {url}: {exc}") from exc

    @cached(TICKER_MAP_TTL)
    def _ticker_to_cik(self) -> dict[str, int]:
        """EDGAR keys everything by CIK, not ticker. One ~10k-entry file covers
        the whole market, so it is fetched once and held for a week."""
        try:
            payload = json.loads(self._fetch(TICKER_MAP_URL).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise DataProviderError(f"sec_edgar ticker map was not valid JSON: {exc}") from exc
        return {
            str(entry["ticker"]).upper(): int(entry["cik_str"])
            for entry in payload.values()
            if entry.get("ticker") and entry.get("cik_str") is not None
        }

    def _recent_form4_filings(self, cik: int, since: date) -> list[tuple[str, str]]:
        """(accession, document) for Form 4 filings on or after `since`."""
        try:
            payload = json.loads(self._fetch(SUBMISSIONS_URL.format(cik=cik)).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise DataProviderError(f"sec_edgar submissions was not valid JSON: {exc}") from exc

        recent = (payload.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        documents = recent.get("primaryDocument") or []

        out: list[tuple[str, str]] = []
        for index, form in enumerate(forms):
            if form != "4":
                continue
            try:
                filed = datetime.strptime(dates[index], "%Y-%m-%d").date()
            except (ValueError, IndexError):
                continue
            if filed < since:
                continue
            try:
                accession = accessions[index].replace("-", "")
                document = _XSL_PREFIX_RE.sub("", documents[index])
            except IndexError:
                continue
            out.append((accession, document))
            if len(out) >= MAX_FILINGS_TO_READ:
                break
        return out

    @staticmethod
    def _parse_form4(xml: str) -> tuple[int, int, float, float]:
        """-> (buy_count, sell_count, buy_value, sell_value) for one filing.

        Parsed with regex rather than an XML parser on purpose: Form 4
        documents vary in namespacing and nesting across filing agents, and
        every field of interest is a flat, unambiguous tag. A missing price
        (some filings report share counts only) contributes to the count but
        not the value, rather than being scored as a $0 trade.
        """
        codes = _TRANSACTION_CODE_RE.findall(xml)
        shares = _SHARES_RE.findall(xml)
        prices = _PRICE_RE.findall(xml)

        buy_count = sell_count = 0
        buy_value = sell_value = 0.0
        for index, code in enumerate(codes):
            if code not in (OPEN_MARKET_BUY, OPEN_MARKET_SELL):
                continue
            try:
                share_count = float(shares[index])
            except (IndexError, ValueError):
                continue
            try:
                price = float(prices[index]) if prices[index] else 0.0
            except (IndexError, ValueError):
                price = 0.0
            if code == OPEN_MARKET_BUY:
                buy_count += 1
                buy_value += share_count * price
            else:
                sell_count += 1
                sell_value += share_count * price
        return buy_count, sell_count, buy_value, sell_value

    @cached(INSIDER_TTL)
    def get_insider_activity(self, symbol: str) -> InsiderActivity | None:
        cik_by_ticker = self._ticker_to_cik()
        cik = cik_by_ticker.get(symbol.upper())
        if cik is None:
            # Not an SEC registrant: crypto pairs, indices, foreign tickers.
            # A genuine absence, not a failure — the scorer treats it as 0.
            return None

        since = date.today() - timedelta(days=INSIDER_WINDOW_DAYS)
        filings = self._recent_form4_filings(cik, since)

        buy_count = sell_count = 0
        buy_value = sell_value = 0.0
        for accession, document in filings:
            url = ARCHIVE_URL.format(cik=cik, accession=accession, document=document)
            try:
                xml = self._fetch(url).decode("utf-8", "replace")
            except DataProviderError:
                continue  # one unreadable filing must not void the whole window
            b_count, s_count, b_value, s_value = self._parse_form4(xml)
            buy_count += b_count
            sell_count += s_count
            buy_value += b_value
            sell_value += s_value

        return InsiderActivity(
            symbol=symbol.upper(),
            window_days=INSIDER_WINDOW_DAYS,
            buy_count=buy_count,
            sell_count=sell_count,
            buy_value=buy_value,
            sell_value=sell_value,
        )

    # --- EDGAR is not a market-data source; everything else falls through ---

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        raise NotImplementedError("sec_edgar provides insider filings only")

    def get_quote(self, symbol: str) -> QuoteData:
        raise NotImplementedError("sec_edgar provides insider filings only")

    def get_company_overview(self, symbol: str) -> CompanyOverview:
        raise NotImplementedError("sec_edgar provides insider filings only")

    def get_financials(self, symbol: str) -> FinancialsData:
        raise NotImplementedError("sec_edgar provides insider filings only")

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        raise NotImplementedError("sec_edgar provides insider filings only")

    def get_earnings_date(self, symbol: str) -> date | None:
        raise NotImplementedError("sec_edgar provides insider filings only")

    def get_earnings_estimate(self, symbol: str):
        raise NotImplementedError("sec_edgar provides insider filings only")

    def get_options_summary(self, symbol: str) -> OptionsSummary:
        raise NotImplementedError("sec_edgar provides insider filings only")
