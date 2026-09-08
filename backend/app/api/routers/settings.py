from __future__ import annotations

from fastapi import APIRouter

from app.config import load_app_settings, update_app_settings
from app.data_providers.finnhub_provider import FinnhubProvider
from app.data_providers.base import DataProviderError
from app.llm_providers.factory import get_llm_provider
from app.schemas.settings_schemas import (
    SettingsUpdateRequest,
    StatusResponse,
    TestConnectionRequest,
    TestConnectionResponse,
)
from app.services.telegram_service import send_message as send_telegram_message

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings() -> dict:
    return load_app_settings().redacted()


@router.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    """Cheap, poll-friendly status for the sidebar's online indicators.

    Deliberately does NOT make a live network call to the LLM or Finnhub on
    every poll (that has real latency/cost, especially for a metered LLM
    key) — "online" here means "configured such that a real call would be
    attempted", mirroring how CompositeDataProvider/get_llm_provider decide
    whether to use a provider at all. `none`/NullLLMProvider always reports
    is_configured() == True (it's the universal fallback) but is explicitly
    excluded here since it's just an echo, not real AI.
    """
    settings = load_app_settings()
    provider = get_llm_provider(settings)
    ai_online = provider.name != "none" and provider.is_configured()
    finnhub_online = bool(settings.finnhub_enabled and settings.finnhub_api_key)
    telegram_online = bool(settings.telegram_bot_token and settings.telegram_chat_id)
    return StatusResponse(
        ai_online=ai_online, ai_provider=provider.name, finnhub_online=finnhub_online, telegram_online=telegram_online
    )


@router.put("")
def put_settings(req: SettingsUpdateRequest) -> dict:
    changes = {k: v for k, v in req.model_dump().items() if v is not None}
    updated = update_app_settings(**changes)
    return updated.redacted()


@router.post("/test-connection", response_model=TestConnectionResponse)
def test_connection(req: TestConnectionRequest) -> TestConnectionResponse:
    settings = load_app_settings()

    if req.target == "llm":
        provider = get_llm_provider(settings)
        if not provider.is_configured():
            return TestConnectionResponse(ok=False, message=f"{provider.name} is not configured")
        result = provider.generate("Reply with exactly: OK", max_tokens=10)
        if result.error:
            return TestConnectionResponse(ok=False, message=result.error)
        return TestConnectionResponse(ok=True, message=f"{provider.name} responded in {result.latency_ms}ms")

    if req.target == "finnhub":
        if not settings.finnhub_api_key:
            return TestConnectionResponse(ok=False, message="No Finnhub API key configured")
        try:
            FinnhubProvider(settings.finnhub_api_key).get_quote("AAPL")
        except DataProviderError as exc:
            return TestConnectionResponse(ok=False, message=str(exc))
        return TestConnectionResponse(ok=True, message="Finnhub connection OK")

    if req.target == "telegram":
        result = send_telegram_message(
            settings.telegram_bot_token, settings.telegram_chat_id, "Strategeia: test connection OK."
        )
        return TestConnectionResponse(ok=result.ok, message=result.message)

    return TestConnectionResponse(ok=False, message=f"Unknown test target: {req.target}")
