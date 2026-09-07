from __future__ import annotations

from app.config import AppSettings, load_app_settings
from app.data_providers.base import DataProvider
from app.data_providers.factory import get_data_provider as _build_data_provider
from app.llm_providers.base import LLMProvider
from app.llm_providers.factory import get_llm_provider as _build_llm_provider

# Re-exported so routers only import from app.api.deps
from app.database import get_session  # noqa: F401


def get_app_settings() -> AppSettings:
    return load_app_settings()


def get_data_provider() -> DataProvider:
    return _build_data_provider(load_app_settings())


def get_llm_provider() -> LLMProvider:
    return _build_llm_provider(load_app_settings())
