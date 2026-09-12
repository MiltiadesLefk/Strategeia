from __future__ import annotations

from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.composite_provider import CompositeDataProvider
from app.data_providers.finnhub_provider import FinnhubProvider
from app.data_providers.nasdaq_provider import NasdaqProvider
from app.data_providers.stooq_provider import StooqProvider
from app.data_providers.yfinance_provider import YFinanceProvider


def get_data_provider(settings: AppSettings) -> CompositeDataProvider:
    """Order is the fallback chain, tried per-method (see CompositeDataProvider).

    yfinance stays primary on measured speed, not habit: ~0.14s for a year of
    daily bars and ~0.9s for a quote, against Nasdaq's ~1.5s and 1.2-4.5s. An
    auto-scan pass is roughly ten calls per symbol across the universe, so that
    gap is minutes per scan — the hot path has to be the fast one.

    Nasdaq sits second because it transforms the *degraded* mode. Previously a
    Yahoo outage fell straight through to Stooq, which serves OHLCV and nothing
    else, so quotes and fundamentals simply disappeared until Yahoo recovered
    (the standing complaint in notes/Issues.md). Nasdaq covers quotes, daily
    candles and company overview, needs no key, and is a completely separate
    operator — so it fails independently of Yahoo, which is the only property
    that makes a fallback worth having.

    Stooq remains last: OHLCV-only, but a third independent source costs
    nothing and covers the case where both of the others are down.
    """
    providers: list[DataProvider] = []
    if settings.finnhub_enabled and settings.finnhub_api_key:
        providers.append(FinnhubProvider(settings.finnhub_api_key))
    providers.append(YFinanceProvider())
    providers.append(NasdaqProvider())
    providers.append(StooqProvider())
    return CompositeDataProvider(providers)
