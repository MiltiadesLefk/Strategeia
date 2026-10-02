from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from app.analysis.onboarding import OnboardingAnswers, OnboardingSuggestion, suggest
from app.api.deps import require_auth
from app.config import load_app_settings, update_app_settings
from app.data_providers.finnhub_provider import FinnhubProvider
from app.data_providers.base import DataProviderError
from app.llm_providers.factory import generate_with_tier, get_llm_provider
from app.settings_overrides import SECRET_FIELDS, apply_test_overrides, is_masked_secret, scrub_secrets
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


def _distinct_decision_model(provider) -> str:
    """The provider's decision-tier model when it differs from its routine
    one, else "" (a blank decision model means both tiers share one model, so
    there is nothing extra to show)."""
    model_for = getattr(provider, "model_for", None)
    if model_for is None:
        return ""
    decision = model_for("decision")
    return decision if decision != model_for("routine") else ""


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
        ai_decision_model=_distinct_decision_model(provider) if ai_online else "",
        ai_overlay_online=ai_overlay_online,
        finnhub_online=finnhub_online,
        telegram_online=telegram_online,
    )


@router.post("/onboarding-suggestion", response_model=OnboardingSuggestion)
def onboarding_suggestion(answers: OnboardingAnswers) -> OnboardingSuggestion:
    """A suggested style and risk percentage from the questionnaire. Computes
    only: nothing is saved and no setting changes."""
    return suggest(answers)


@router.put("")
def put_settings(req: SettingsUpdateRequest) -> dict:
    changes = {k: v for k, v in req.model_dump().items() if v is not None}
    # A masked hint ("••••ab12") is what the API shows for a saved secret; a
    # client that echoes it back means "unchanged", so it must never overwrite
    # the real secret with the hint.
    changes = {k: v for k, v in changes.items() if not (k in SECRET_FIELDS and is_masked_secret(v))}
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


def _llm_ok_message(provider, result, tier: str = "routine") -> str:
    """"claude_code_cli responded in 1300ms (model: claude-sonnet-5-5, pinned to
    'sonnet')". The model the call actually used is what you want to see after
    changing the pin, so say it when the provider reports one. A decision-tier
    test says so up front, and says when no decision model is set (the call
    then went to the routine model)."""
    message = f"{provider.name} responded in {result.latency_ms}ms"
    model_for = getattr(provider, "model_for", None)
    if tier == "decision":
        message = f"{provider.name} decision model responded in {result.latency_ms}ms"
        if model_for is not None and model_for("decision") == model_for("routine"):
            message += " (no decision model set, so this used the routine model)"
    pinned = model_for(tier) if model_for is not None else getattr(provider, "model", "")
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
    # The two LLM tiers hit different models (and costs), so each has its own
    # cooldown; every other target is keyed by its name as before.
    cooldown_key = f"{req.target}:{req.tier}" if req.target == "llm" and req.tier != "routine" else req.target
    last = _last_test_connection_monotonic.get(cooldown_key)
    if last is not None:
        elapsed = now - last
        if elapsed < TEST_CONNECTION_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"'{req.target}' was just tested {elapsed:.0f}s ago — wait "
                f"{TEST_CONNECTION_COOLDOWN_SECONDS - elapsed:.0f}s before testing it again.",
            )
    _last_test_connection_monotonic[cooldown_key] = now

    # The saved settings with whatever is typed in the form on top, used for this
    # one call and then dropped: nothing is saved, and nothing here is logged.
    saved = load_app_settings()
    overrides = req.overrides.model_dump() if req.overrides else {}
    try:
        settings = apply_test_overrides(saved, overrides)
    except ValueError as exc:  # a typed value that would not pass the save-time checks either
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = _run_connection_test(req, settings)
    # An error from a provider or from httpx can echo the request URL, and a
    # Telegram URL contains the bot token: never hand a secret back to the browser.
    secrets = [getattr(settings, field, "") for field in SECRET_FIELDS]
    response.message = scrub_secrets(response.message, secrets)
    return response


def _run_connection_test(req: TestConnectionRequest, settings) -> TestConnectionResponse:
    if req.target == "llm":
        provider = get_llm_provider(settings)
        if not provider.is_configured():
            return TestConnectionResponse(ok=False, message=f"{provider.name} is not configured")
        result = generate_with_tier(provider, "Reply with exactly: OK", req.tier, max_tokens=10)
        if result.error:
            return TestConnectionResponse(ok=False, message=result.error)
        return TestConnectionResponse(ok=True, message=_llm_ok_message(provider, result, req.tier))

    if req.target == "finnhub":
        if not settings.finnhub_api_key:
            return TestConnectionResponse(ok=False, message="No Finnhub API key configured")
        try:
            FinnhubProvider(settings.finnhub_api_key).check_connection()
        except DataProviderError as exc:
            return TestConnectionResponse(ok=False, message=str(exc))
        return TestConnectionResponse(ok=True, message="Finnhub connection OK")

    if req.target == "telegram":
        result = send_telegram_message(
            settings.telegram_bot_token, settings.telegram_chat_id, "Strategeia: test connection OK."
        )
        return TestConnectionResponse(ok=result.ok, message=result.message)

    return TestConnectionResponse(ok=False, message=f"Unknown test target: {req.target}")
