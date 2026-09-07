from __future__ import annotations

from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.composite_provider import CompositeDataProvider
from app.data_providers.finnhub_provider import FinnhubProvider
from app.data_providers.stooq_provider import StooqProvider
from app.data_providers.yfinance_provider import YFinanceProvider


def get_data_provider(settings: AppSettings) -> CompositeDataProvider:
    providers: list[DataProvider] = []
    if settings.finnhub_enabled and settings.finnhub_api_key:
        providers.append(FinnhubProvider(settings.finnhub_api_key))
    providers.append(YFinanceProvider())
    providers.append(StooqProvider())
    return CompositeDataProvider(providers)
