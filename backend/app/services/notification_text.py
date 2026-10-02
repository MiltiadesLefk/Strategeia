"""Shared plumbing for the Telegram notifications: fitting a long note into messages,
sending it and keeping the "already sent" record.

Telegram refuses a message over 4096 characters, so a long note is cut at line breaks
into at most MAX_PARTS messages; anything beyond that is replaced by a short marker
instead of being sent as a flood. Messages are plain text: nothing here asks Telegram to
interpret markup, so a headline with odd characters cannot break a send.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session

from app.config import AppSettings
from app.portfolio.alert_models import NotificationLog
from app.services.notification_schedule import market_day_text
from app.services.telegram_service import TelegramResult, send_message
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
# Kept under the hard limit so a part never lands on it exactly.
PART_LIMIT = 3900
MAX_PARTS = 3
SHORTENED_MARKER = "... (note shortened to fit Telegram)"
MAX_ERROR_LENGTH = 300

Sender = Callable[[str, str, str], TelegramResult]


def telegram_configured(settings: AppSettings) -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def split_for_telegram(text: str, *, part_limit: int = PART_LIMIT, max_parts: int = MAX_PARTS) -> list[str]:
    """Cut `text` at line breaks into parts of at most `part_limit` characters. A single
    line longer than that is cut hard. At most `max_parts` parts come back; when the text
    needed more, the last part ends with SHORTENED_MARKER."""
    lines: list[str] = []
    for line in text.strip().splitlines():
        while len(line) > part_limit:
            lines.append(line[:part_limit])
            line = line[part_limit:]
        lines.append(line)

    parts: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= part_limit:
            current = candidate
            continue
        parts.append(current)
        current = line
    if current or not parts:
        parts.append(current)

    if len(parts) > max_parts:
        parts = parts[:max_parts]
        room = part_limit - len(SHORTENED_MARKER) - 1
        parts[-1] = parts[-1][:room].rstrip() + "\n" + SHORTENED_MARKER
    return parts


@dataclass
class SendOutcome:
    ok: bool
    parts_sent: int
    message: str


def send_text(settings: AppSettings, text: str, sender: Sender = send_message) -> SendOutcome:
    """Send `text` to the configured Telegram chat, in parts when needed. Never raises.
    Stops at the first part that fails and says so."""
    if not telegram_configured(settings):
        return SendOutcome(False, 0, "Telegram is not configured (a bot token and a chat id are both needed).")
    sent = 0
    for part in split_for_telegram(text):
        try:
            result = sender(settings.telegram_bot_token, settings.telegram_chat_id, part)
        except Exception as exc:  # noqa: BLE001 - a notification must never raise into the scheduler
            logger.warning("Telegram send raised %s", type(exc).__name__)
            return SendOutcome(False, sent, "Telegram send failed unexpectedly.")
        if not result.ok:
            # The failure text can echo the request URL, which holds the bot token: never keep it.
            logger.warning("Telegram did not accept a notification")
            return SendOutcome(False, sent, "Telegram did not accept the message.")
        sent += 1
    return SendOutcome(True, sent, f"Sent in {sent} message{'s' if sent != 1 else ''}.")


def last_sent_day(session: Session, key: str) -> str | None:
    row = session.get(NotificationLog, key)
    return row.last_day if row else None


# After a failed send the note is tried again no sooner than this, so a Telegram outage
# does not rebuild the note (and spend an AI call) on every scheduler tick.
RETRY_DELAY = timedelta(minutes=30)


def retry_pause_active(session: Session, key: str, now: datetime) -> bool:
    row = session.get(NotificationLog, key)
    return bool(row and row.last_error and row.last_attempt_at and now < row.last_attempt_at + RETRY_DELAY)


def mark_sent(session: Session, key: str, now: datetime | None = None, *, error: str | None = None) -> None:
    """Record a send for `key` (dated by the US Eastern day). With `error`, only the
    error is stored and the day is left alone, so the next tick tries again."""
    now = now if now is not None else utcnow_naive()
    row = session.get(NotificationLog, key) or NotificationLog(key=key)
    row.last_attempt_at = now
    if error is None:
        row.last_day = market_day_text(now)
        row.last_sent_at = now
        row.last_error = None
    else:
        row.last_error = error[:MAX_ERROR_LENGTH]
    session.add(row)
    session.commit()
