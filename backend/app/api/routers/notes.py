from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.deps import get_app_settings, get_data_provider, get_llm_provider, get_session, require_auth
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.schemas.alert_schemas import NotePreviewResponse, NoteSendResponse
from app.services.morning_note_service import generate_morning_note
from app.services.note_common import NoteResult
from app.services.notification_text import send_text, split_for_telegram, telegram_configured
from app.services.weekly_digest_service import generate_weekly_digest

router = APIRouter(prefix="/api/notes", tags=["notes"], dependencies=[Depends(require_auth)])

# Building a note reads quotes for several symbols and a watchlist scan, and sending one
# spends a Telegram message (and, with an AI configured, a model call): not a button to
# mash. Same in-process, per-kind cooldown as the other expensive routes.
NOTE_SEND_COOLDOWN_SECONDS = 60
_last_note_send_monotonic: dict[str, float] = {}
NO_TELEGRAM = "Telegram is not configured (a bot token and a chat id are both needed)."


def _claim_send(kind: str) -> None:
    now = time.monotonic()
    last = _last_note_send_monotonic.get(kind)
    if last is not None and now - last < NOTE_SEND_COOLDOWN_SECONDS:
        raise HTTPException(
            status_code=429,
            detail=f"The {kind} note was just sent {now - last:.0f}s ago: wait "
            f"{NOTE_SEND_COOLDOWN_SECONDS - (now - last):.0f}s before sending it again.",
        )
    _last_note_send_monotonic[kind] = now


def _preview(note: NoteResult) -> NotePreviewResponse:
    return NotePreviewResponse(
        text=note.text,
        ai_used=note.ai_used,
        ai_provider=note.ai_provider,
        unavailable=note.unavailable,
        generated_at=note.generated_at,
        parts=len(split_for_telegram(note.text)),
    )


def _send(settings: AppSettings, note: NoteResult) -> NoteSendResponse:
    outcome = send_text(settings, note.text)
    return NoteSendResponse(ok=outcome.ok, message=outcome.message, parts_sent=outcome.parts_sent, ai_used=note.ai_used)


@router.post("/morning/preview", response_model=NotePreviewResponse)
def preview_morning_note(
    ai: bool = Query(False, description="Add the AI paragraph (spends one model call when an AI is configured)"),
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> NotePreviewResponse:
    """The morning note as it would be sent now. Nothing is sent or stored."""
    return _preview(generate_morning_note(session, settings, data_provider, llm_provider, use_ai=ai))


@router.post("/morning/send", response_model=NoteSendResponse)
def send_morning_note(
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> NoteSendResponse:
    """Send the morning note to Telegram now (any day; the schedule is not consulted)."""
    if not telegram_configured(settings):
        raise HTTPException(status_code=409, detail=NO_TELEGRAM)
    _claim_send("morning")
    return _send(settings, generate_morning_note(session, settings, data_provider, llm_provider, use_ai=True))


@router.post("/weekly/preview", response_model=NotePreviewResponse)
def preview_weekly_digest(
    ai: bool = Query(False, description="Add the AI paragraph (spends one model call when an AI is configured)"),
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> NotePreviewResponse:
    """The weekly digest as it would be sent now. Nothing is sent or stored."""
    return _preview(generate_weekly_digest(session, settings, data_provider, llm_provider, use_ai=ai))


@router.post("/weekly/send", response_model=NoteSendResponse)
def send_weekly_digest(
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> NoteSendResponse:
    """Send the weekly digest to Telegram now (any day)."""
    if not telegram_configured(settings):
        raise HTTPException(status_code=409, detail=NO_TELEGRAM)
    _claim_send("weekly")
    return _send(settings, generate_weekly_digest(session, settings, data_provider, llm_provider, use_ai=True))
