from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
TIMEOUT_SECONDS = 10


@dataclass
class TelegramResult:
    ok: bool
    message: str


def send_message(bot_token: str, chat_id: str, text: str) -> TelegramResult:
    """Low-level send with a detailed result — used by /test-connection so the
    user can see *why* a send failed. Never raises: network/API errors are
    caught and returned as TelegramResult(ok=False, ...), same spirit as the
    llm/finnhub test-connection branches in settings.py."""
    if not bot_token:
        return TelegramResult(False, "No Telegram bot token configured")
    if not chat_id:
        return TelegramResult(False, "No Telegram chat id configured")

    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    try:
        resp = httpx.post(url, json={"chat_id": chat_id, "text": text}, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        return TelegramResult(False, f"Telegram request failed: {exc}")

    if not data.get("ok"):
        return TelegramResult(False, f"Telegram API error: {data.get('description', 'unknown error')}")
    return TelegramResult(True, "Telegram message sent")


def notify(bot_token: str, chat_id: str, text: str) -> None:
    """Best-effort notification hook, generic over the caller's purpose.
    Follows the same graceful-degradation spirit as `generate_with_fallback`:
    a missing token/chat id is a silent no-op (Telegram just isn't
    configured), and an API/network error is logged and swallowed — never
    raised up into the caller, since a Telegram outage must never break
    whatever real work triggered the notification."""
    if not bot_token or not chat_id:
        return
    result = send_message(bot_token, chat_id, text)
    if not result.ok:
        logger.warning("Telegram notification failed: %s", result.message)


def notify_trade_plan(bot_token: str, chat_id: str, text: str) -> None:
    notify(bot_token, chat_id, text)
