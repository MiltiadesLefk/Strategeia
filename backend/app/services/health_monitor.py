from __future__ import annotations

import logging

from app.config import AppSettings
from app.data_providers.base import AllProvidersFailedError, DataProvider
from app.llm_providers.factory import get_llm_provider
from app.services.telegram_service import notify

logger = logging.getLogger(__name__)

# A liquid, always-listed symbol used purely as a connectivity probe — not
# meaningful as market data, just something every provider should be able
# to quote if the pipeline is actually up.
HEALTH_CHECK_SYMBOL = "SPY"

LABELS = {"ai": "AI narrative provider", "finnhub": "Finnhub", "market_data": "Market data providers"}

# Module-level so consecutive scheduler ticks share it; reset by restarting
# the process. Not persisted — a fresh baseline on every app start is fine
# since it only gates duplicate alerts within a single run.
_last_status: dict[str, bool] = {}


def _current_status(settings: AppSettings, data_provider: DataProvider) -> dict[str, bool]:
    provider = get_llm_provider(settings)
    ai_online = provider.name != "none" and provider.is_configured()
    finnhub_online = bool(settings.finnhub_enabled and settings.finnhub_api_key)
    try:
        data_provider.get_quote(HEALTH_CHECK_SYMBOL)
        market_data_online = True
    except AllProvidersFailedError:
        market_data_online = False
    return {"ai": ai_online, "finnhub": finnhub_online, "market_data": market_data_online}


def check_and_alert(settings: AppSettings, data_provider: DataProvider) -> dict[str, bool]:
    """Compares current provider status against the last observed status and
    sends a Telegram alert only on a transition (online->offline or the
    reverse) — never on steady state, so a provider that's intentionally
    never configured (e.g. Finnhub unused) doesn't spam a message every
    tick. The very first call just seeds the baseline silently. Never
    raises: a health check that itself breaks must not take down the
    scheduler tick that called it."""
    global _last_status
    try:
        current = _current_status(settings, data_provider)
    except Exception:
        logger.exception("Health check itself failed")
        return _last_status

    if _last_status:
        for key, online in current.items():
            previous = _last_status.get(key)
            if previous is None or previous == online:
                continue
            label = LABELS[key]
            text = f"✅ Strategeia: {label} is back online." if online else f"⚠️ Strategeia: {label} looks offline/broken — check Settings."
            notify(settings.telegram_bot_token, settings.telegram_chat_id, text)

    _last_status = current
    return current
