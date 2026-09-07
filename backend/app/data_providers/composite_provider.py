from __future__ import annotations

from datetime import date

import pandas as pd

from app.data_providers.base import (
    AllProvidersFailedError,
    CompanyOverview,
    DataProvider,
    DataProviderError,
    FinancialsData,
    NewsItem,
    QuoteData,
)


class CompositeDataProvider:
    """Tries providers in order for each method; caches happen inside each
    provider. Never fabricates data: if every provider fails, raises
    AllProvidersFailedError so the API can surface a clear error/stale badge
    instead of silently returning wrong numbers."""

    name = "composite"

    def __init__(self, providers: list[DataProvider]):
        if not providers:
            raise ValueError("CompositeDataProvider requires at least one provider")
        self._providers = providers

    def _try_each(self, method_name: str, *args, **kwargs):
        errors: list[str] = []
        for provider in self._providers:
            method = getattr(provider, method_name)
            try:
                return method(*args, **kwargs)
            except NotImplementedError:
                continue
            except DataProviderError as exc:
                errors.append(f"{provider.name}: {exc}")
                continue
        raise AllProvidersFailedError(
            f"All providers failed for {method_name}({args}, {kwargs}): {'; '.join(errors) or 'no provider implements this'}"
        )

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        return self._try_each("get_ohlcv", symbol, period=period, interval=interval)

    def get_quote(self, symbol: str) -> QuoteData:
        return self._try_each("get_quote", symbol)

    def get_company_overview(self, symbol: str) -> CompanyOverview:
        return self._try_each("get_company_overview", symbol)

    def get_financials(self, symbol: str) -> FinancialsData:
        return self._try_each("get_financials", symbol)

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        return self._try_each("get_news", symbol, limit=limit)

    def get_earnings_date(self, symbol: str) -> date | None:
        try:
            return self._try_each("get_earnings_date", symbol)
        except AllProvidersFailedError:
            return None
