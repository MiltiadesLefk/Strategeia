from __future__ import annotations

from datetime import date

import pandas as pd

from app.data_providers.base import (
    AllProvidersFailedError,
    CompanyOverview,
    DataProvider,
    DataProviderError,
    EarningsEstimate,
    InsiderActivity,
    FinancialsData,
    NewsItem,
    OptionsSummary,
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

    def get_earnings_estimate(self, symbol: str) -> EarningsEstimate | None:
        try:
            return self._try_each("get_earnings_estimate", symbol)
        except AllProvidersFailedError:
            return None

    def get_insider_activity(self, symbol: str) -> InsiderActivity | None:
        # Same "absence is not an error" shape as get_options_summary: a crypto
        # pair or any non-SEC-registrant legitimately has no Form 4 history.
        try:
            return self._try_each("get_insider_activity", symbol)
        except AllProvidersFailedError:
            return None

    def get_options_summary(self, symbol: str) -> OptionsSummary | None:
        # Common/expected absence (crypto has no options chain at all, many
        # smaller names have none either) — same "return None, don't raise"
        # shape as get_earnings_date/get_earnings_estimate above, not an
        # error condition callers need to handle specially.
        try:
            return self._try_each("get_options_summary", symbol)
        except AllProvidersFailedError:
            return None
