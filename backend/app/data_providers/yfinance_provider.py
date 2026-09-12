from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import yfinance as yf

from app.data_providers.base import (
    CompanyOverview,
    DataProviderError,
    EarningsEstimate,
    FinancialsData,
    FinancialYear,
    NewsItem,
    OptionsSummary,
    QuoteData,
)
from app.data_providers.cache import cached
from app.timeutil import utc_from_timestamp_naive, utc_iso_from_timestamp

# Deliberately short, and deliberately the same as QUOTE_TTL. This was 24h,
# which quietly broke the exit engine: mark_to_market runs every 15 min but
# was re-reading ONE cached fetch for a full day, so a bar first pulled
# mid-session kept its half-formed high/low until the next day — stops and
# targets were evaluated against a snapshot of the market that had already
# moved on. The cache's real value here is deduplicating within a single
# scan run (one auto-scan pass asks for SPY and ^VIX once per symbol — 50
# symbols collapse to 1 fetch each), and a 15-minute window still does all
# of that while never serving a bar older than one mark-to-market tick.
OHLCV_TTL = 15 * 60
QUOTE_TTL = 15 * 60
OVERVIEW_TTL = 24 * 60 * 60
FINANCIALS_TTL = 24 * 60 * 60
NEWS_TTL = 30 * 60
EARNINGS_TTL = 24 * 60 * 60
OPTIONS_TTL = 30 * 60

# Same-day/next-day expirations carry unreliable implied-volatility quotes
# on free data (thin/stale prints at the bid-ask floor) — skip ahead to the
# first expiration at least this many days out.
OPTIONS_MIN_DAYS_TO_EXPIRATION = 7


class YFinanceProvider:
    name = "yfinance"

    @cached(OHLCV_TTL)
    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        try:
            hist = yf.Ticker(symbol).history(period=period, interval=interval)
        except Exception as exc:  # yfinance raises varied exception types across versions
            raise DataProviderError(f"yfinance get_ohlcv({symbol}) failed: {exc}") from exc
        if hist.empty:
            raise DataProviderError(f"yfinance returned no OHLCV data for {symbol}")
        hist = hist.rename(
            columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
        )
        hist = hist.reset_index(drop=False)
        hist = hist.rename(columns={hist.columns[0]: "date"})  # index name varies: Date/Datetime
        return hist[["date", "open", "high", "low", "close", "volume"]]

    @cached(QUOTE_TTL)
    def get_quote(self, symbol: str) -> QuoteData:
        try:
            hist = yf.Ticker(symbol).history(period="1mo", interval="1d")
        except Exception as exc:
            raise DataProviderError(f"yfinance get_quote({symbol}) failed: {exc}") from exc
        if hist.empty or len(hist) < 2:
            raise DataProviderError(f"yfinance returned insufficient history for {symbol}")
        last_close = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2])
        change_pct = (last_close - prev_close) / prev_close * 100 if prev_close else 0.0
        avg_volume = float(hist["Volume"].tail(20).mean())
        return QuoteData(
            symbol=symbol,
            price=last_close,
            change_pct_24h=change_pct,
            volume=float(hist["Volume"].iloc[-1]),
            avg_volume_20d=avg_volume,
        )

    @cached(OVERVIEW_TTL)
    def get_company_overview(self, symbol: str) -> CompanyOverview:
        try:
            info = yf.Ticker(symbol).info
        except Exception as exc:
            raise DataProviderError(f"yfinance get_company_overview({symbol}) failed: {exc}") from exc
        if not info:
            raise DataProviderError(f"yfinance returned no info for {symbol}")
        return CompanyOverview(
            symbol=symbol,
            name=info.get("longName") or info.get("shortName") or symbol,
            market_cap=info.get("marketCap"),
            pe_ratio=info.get("trailingPE"),
            revenue_ttm=info.get("totalRevenue"),
            eps_ttm=info.get("trailingEps"),
            week52_low=info.get("fiftyTwoWeekLow"),
            week52_high=info.get("fiftyTwoWeekHigh"),
        )

    @cached(FINANCIALS_TTL)
    def get_financials(self, symbol: str) -> FinancialsData:
        try:
            ticker = yf.Ticker(symbol)
            financials = ticker.financials
        except Exception as exc:
            raise DataProviderError(f"yfinance get_financials({symbol}) failed: {exc}") from exc
        if financials is None or financials.empty:
            raise DataProviderError(f"yfinance returned no financials for {symbol}")

        years: list[FinancialYear] = []
        for column in financials.columns:
            try:
                revenue = float(financials.at["Total Revenue", column])
                net_income = float(financials.at["Net Income", column])
            except KeyError:
                continue
            if pd.isna(revenue) or pd.isna(net_income):
                continue  # yfinance sometimes reports a trailing year with no data yet
            years.append(FinancialYear(year=pd.Timestamp(column).year, revenue=revenue, net_income=net_income))
        years.sort(key=lambda fy: fy.year)
        if not years:
            raise DataProviderError(f"yfinance financials for {symbol} missing Revenue/Net Income rows")
        return FinancialsData(symbol=symbol, years=years)

    @cached(NEWS_TTL)
    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        try:
            raw_news = yf.Ticker(symbol).news or []
        except Exception as exc:
            raise DataProviderError(f"yfinance get_news({symbol}) failed: {exc}") from exc

        items: list[NewsItem] = []
        for entry in raw_news[:limit]:
            content = entry.get("content", entry)
            headline = content.get("title") or entry.get("title") or ""
            source = (content.get("provider") or {}).get("displayName") or entry.get("publisher") or "Unknown"
            url = (content.get("canonicalUrl") or {}).get("url") or entry.get("link") or ""
            published_raw = content.get("pubDate") or entry.get("providerPublishTime")
            if isinstance(published_raw, (int, float)):
                published_at = utc_iso_from_timestamp(published_raw)
            else:
                published_at = str(published_raw or "")
            if headline:
                items.append(NewsItem(headline=headline, source=source, url=url, published_at=published_at))
        return items

    @cached(EARNINGS_TTL)
    def get_earnings_date(self, symbol: str) -> date | None:
        try:
            dates_df = yf.Ticker(symbol).get_earnings_dates(limit=8)
        except Exception:
            return None
        if dates_df is None or dates_df.empty:
            return None
        upcoming = [ts for ts in dates_df.index if ts.date() >= date.today()]
        if not upcoming:
            return None
        return min(upcoming).date()

    @cached(EARNINGS_TTL)
    def get_earnings_estimate(self, symbol: str) -> EarningsEstimate | None:
        # yfinance's own `get_earnings_dates()` carries an "EPS Estimate"
        # column alongside each date — no separate call needed. Revenue
        # estimate isn't in this frame; left None rather than guessed at.
        try:
            dates_df = yf.Ticker(symbol).get_earnings_dates(limit=8)
        except Exception:
            return None
        if dates_df is None or dates_df.empty:
            return None
        upcoming = [ts for ts in dates_df.index if ts.date() >= date.today()]
        if not upcoming:
            return None
        next_ts = min(upcoming)
        row = dates_df.loc[next_ts]
        if isinstance(row, pd.DataFrame):  # duplicate index timestamps, rare
            row = row.iloc[0]
        eps_estimate = row.get("EPS Estimate")
        eps_estimate = float(eps_estimate) if eps_estimate is not None and not pd.isna(eps_estimate) else None
        quarter = (next_ts.month - 1) // 3 + 1
        return EarningsEstimate(
            date=next_ts.date(),
            fiscal_period_label=f"Q{quarter} {next_ts.year}",
            eps_estimate=eps_estimate,
            revenue_estimate=None,
        )

    @cached(OPTIONS_TTL)
    def get_options_summary(self, symbol: str) -> OptionsSummary:
        try:
            ticker = yf.Ticker(symbol)
            expirations = ticker.options
        except Exception as exc:
            raise DataProviderError(f"yfinance get_options_summary({symbol}) failed: {exc}") from exc
        if not expirations:
            raise DataProviderError(f"yfinance has no options chain for {symbol}")

        today = date.today()
        expiration = next(
            (e for e in expirations if (datetime.strptime(e, "%Y-%m-%d").date() - today).days >= OPTIONS_MIN_DAYS_TO_EXPIRATION),
            expirations[-1],  # every expiration is too near-dated — use the furthest one available rather than 0DTE noise
        )

        try:
            chain = ticker.option_chain(expiration)
            calls, puts = chain.calls, chain.puts
        except Exception as exc:
            raise DataProviderError(f"yfinance option_chain({symbol}, {expiration}) failed: {exc}") from exc

        call_volume = float(calls["volume"].fillna(0).sum()) if not calls.empty else 0.0
        put_volume = float(puts["volume"].fillna(0).sum()) if not puts.empty else 0.0
        put_call_ratio = put_volume / call_volume if call_volume > 0 else None

        atm_iv = None
        if not calls.empty:
            try:
                spot = float(ticker.fast_info["lastPrice"])
                atm_row = calls.iloc[(calls["strike"] - spot).abs().argsort().iloc[0]]
                iv = atm_row.get("impliedVolatility")
                atm_iv = float(iv) if iv is not None and not pd.isna(iv) else None
            except Exception:
                atm_iv = None  # best-effort only — never let ATM-strike lookup break the whole options summary

        return OptionsSummary(
            symbol=symbol, expiration=expiration, put_call_volume_ratio=put_call_ratio, atm_implied_volatility=atm_iv
        )
