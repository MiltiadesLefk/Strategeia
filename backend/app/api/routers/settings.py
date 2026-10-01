from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_auth
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

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])


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
    # Matches _maybe_get_ai_opinion's own gate in trade_plan_service.py: the
    # overlay only ever actually calls out when the toggle is on AND a real,
    # configured provider is selected — not just whether the checkbox is on.
    ai_overlay_online = settings.ai_trading_overlay_enabled and ai_online
    finnhub_online = bool(settings.finnhub_enabled and settings.finnhub_api_key)
    telegram_online = bool(settings.telegram_bot_token and settings.telegram_chat_id)
    return StatusResponse(
        ai_online=ai_online,
        ai_provider=provider.name,
        # Only providers with a public `model` pin (the Claude CLI) report one.
        ai_model=getattr(provider, "model", "") or "" if ai_online else "",
        ai_overlay_online=ai_overlay_online,
        finnhub_online=finnhub_online,
        telegram_online=telegram_online,
    )


@router.put("")
def put_settings(req: SettingsUpdateRequest) -> dict:
    changes = {k: v for k, v in req.model_dump().items() if v is not None}
    updated = update_app_settings(**changes)
    return updated.redacted()


# Same in-process, not-persisted cooldown pattern as
# scanner.py's AUTO_TRADE_COOLDOWN_SECONDS — this is the one remaining
# endpoint TODO.md flagged as able to trigger real per-call LLM/Finnhub
# spend with no cooldown at all (unlike /api/trade-plans/generate's
# auto-execute path, capped by max_concurrent_positions, and
# /api/scan/auto-trade, which already has its own 60s cooldown). Keyed per
# `target` rather than one shared timer, since testing Telegram shouldn't
# block testing the LLM provider right after — they hit different
# services with different costs and no shared budget to protect. 10s is
# short enough not to annoy someone clicking the button while configuring
# a provider, long enough to stop a scripted loop from draining a metered
# key.
TEST_CONNECTION_COOLDOWN_SECONDS = 10
_last_test_connection_monotonic: dict[str, float] = {}


def _llm_ok_message(provider, result) -> str:
    """"claude_code_cli responded in 1300ms (model: claude-sonnet-5-5, pinned to
    'sonnet')". The model the call actually used is what you want to see after
    changing the pin, so say it when the provider reports one."""
    message = f"{provider.name} responded in {result.latency_ms}ms"
    pinned = getattr(provider, "model", "")
    if result.model and pinned and result.model != pinned:
        return f"{message} (model: {result.model}, pinned to '{pinned}')"
    if result.model:
        return f"{message} (model: {result.model})"
    if provider.name == "claude_code_cli":
        return f"{message} (model: CLI default, not pinned)"
    return message


@router.post("/test-connection", response_model=TestConnectionResponse)
def test_connection(req: TestConnectionRequest) -> TestConnectionResponse:
    now = time.monotonic()
    last = _last_test_connection_monotonic.get(req.target)
    if last is not None:
        elapsed = now - last
        if elapsed < TEST_CONNECTION_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"'{req.target}' was just tested {elapsed:.0f}s ago — wait "
                f"{TEST_CONNECTION_COOLDOWN_SECONDS - elapsed:.0f}s before testing it again.",
            )
    _last_test_connection_monotonic[req.target] = now

    settings = load_app_settings()

    if req.target == "llm":
        provider = get_llm_provider(settings)
        if not provider.is_configured():
            return TestConnectionResponse(ok=False, message=f"{provider.name} is not configured")
        result = provider.generate("Reply with exactly: OK", max_tokens=10)
        if result.error:
            return TestConnectionResponse(ok=False, message=result.error)
        return TestConnectionResponse(ok=True, message=_llm_ok_message(provider, result))

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
