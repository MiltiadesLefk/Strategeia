from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from app.config import AppSettings, get_infra_settings, load_app_settings
from app.data_providers.base import DataProvider
from app.data_providers.factory import get_data_provider as _build_data_provider
from app.llm_providers.base import LLMProvider
from app.llm_providers.factory import get_llm_provider as _build_llm_provider

# Re-exported so routers only import from app.api.deps
from app.database import get_session  # noqa: F401


def get_app_settings() -> AppSettings:
    return load_app_settings()


def require_shared_secret(x_api_key: str | None = Header(default=None)) -> None:
    """Opt-in stop-gap auth gate — see InfraSettings.api_shared_secret. A
    blank secret (the default) makes this a no-op, so local/dev use is
    unaffected; setting API_SHARED_SECRET in .env requires every request to
    carry a matching `X-API-Key` header."""
    expected = get_infra_settings().api_shared_secret
    if not expected:
        return
    # compare_digest, not ==: a plain string comparison short-circuits on the
    # first differing byte, so response timing leaks a correct prefix and the
    # secret can be recovered byte by byte. This is the only auth in front of
    # endpoints that spend LLM/Finnhub credit and can wipe the portfolio.
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")


def get_data_provider() -> DataProvider:
    return _build_data_provider(load_app_settings())


def get_llm_provider() -> LLMProvider:
    return _build_llm_provider(load_app_settings())
