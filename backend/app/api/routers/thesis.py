from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.deps import get_app_settings, get_data_provider, get_llm_provider, get_session, require_auth
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.portfolio.models import PaperPosition
from app.portfolio.thesis_models import ThesisRecord
from app.schemas.thesis_schemas import (
    ThesisItemRequest,
    ThesisItemUpdateRequest,
    ThesisNoteRequest,
    ThesisRecheckResponse,
    ThesisResponse,
    ThesisSchema,
)
from app.services import thesis_service
from app.services.lesson_service import is_real_llm

router = APIRouter(prefix="/api/portfolio/positions", tags=["thesis"], dependencies=[Depends(require_auth)])

# Each review spends a call on the user's AI provider and the button can be double-clicked, so
# a short per-position wait applies (in-process, not persisted, like the lesson button's).
THESIS_REVIEW_COOLDOWN_SECONDS = 20
_last_thesis_review_monotonic: dict[int, float] = {}


def _position(session: Session, position_id: int) -> PaperPosition:
    position = session.get(PaperPosition, position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return position


def _existing_thesis(session: Session, position_id: int) -> ThesisRecord:
    _position(session, position_id)
    thesis = thesis_service.get_thesis(session, position_id)
    if thesis is None:
        raise HTTPException(status_code=404, detail="This position has no thesis yet; run a re-check to create one.")
    return thesis


@router.get("/{position_id}/thesis", response_model=ThesisResponse)
def read_thesis(position_id: int, session: Session = Depends(get_session)) -> ThesisResponse:
    """Read-only: a position with no thesis yet returns `thesis: null` and nothing is created
    (a GET refetched on every window focus must never write). The thesis is created by the
    background sweep, by opening a position by hand, or by the re-check button."""
    _position(session, position_id)
    thesis = thesis_service.get_thesis(session, position_id)
    return ThesisResponse(thesis=thesis_service.thesis_schema(thesis) if thesis else None)


@router.post("/{position_id}/thesis/recheck", response_model=ThesisRecheckResponse)
def recheck(
    position_id: int,
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> ThesisRecheckResponse:
    """Create the thesis if the position has none, then re-check it against fresh data now."""
    position = _position(session, position_id)
    if position.status != "open":
        raise HTTPException(status_code=400, detail="Only an open position has a thesis to re-check.")
    outcome = thesis_service.recheck_thesis(session, position, data_provider, settings)
    thesis = thesis_service.get_thesis(session, position_id)
    assert thesis is not None  # recheck_thesis seeds before it does anything else
    return ThesisRecheckResponse(
        thesis=thesis_service.thesis_schema(thesis), status=outcome.status, changes=outcome.changes, note=outcome.note
    )


@router.post("/{position_id}/thesis/review", response_model=ThesisSchema)
def review(
    position_id: int,
    session: Session = Depends(get_session),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> ThesisSchema:
    """The optional AI paragraph on where the thesis stands. Only ever runs from this button.
    A model failure is not an HTTP error: the thesis comes back with `review_error` set."""
    thesis = _existing_thesis(session, position_id)
    position = _position(session, position_id)
    if not is_real_llm(llm_provider):
        raise HTTPException(status_code=400, detail="No AI provider is configured (Settings -> AI Provider)")
    now = time.monotonic()
    last = _last_thesis_review_monotonic.get(position_id)
    if last is not None and now - last < THESIS_REVIEW_COOLDOWN_SECONDS:
        raise HTTPException(
            status_code=429,
            detail=f"A review was just requested for this thesis; wait "
            f"{THESIS_REVIEW_COOLDOWN_SECONDS - (now - last):.0f}s before asking again.",
        )
    _last_thesis_review_monotonic[position_id] = now
    thesis_service.generate_thesis_review(session, position, thesis, llm_provider)
    return thesis_service.thesis_schema(thesis)


@router.post("/{position_id}/thesis/notes", response_model=ThesisSchema)
def add_note(position_id: int, req: ThesisNoteRequest, session: Session = Depends(get_session)) -> ThesisSchema:
    thesis = _existing_thesis(session, position_id)
    try:
        thesis_service.add_note(session, thesis, req.text)
    except thesis_service.ThesisEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return thesis_service.thesis_schema(thesis)


@router.post("/{position_id}/thesis/items", response_model=ThesisSchema)
def add_item(position_id: int, req: ThesisItemRequest, session: Session = Depends(get_session)) -> ThesisSchema:
    thesis = _existing_thesis(session, position_id)
    try:
        thesis_service.add_item(session, thesis, req.kind, req.text, when_date=req.date, status=req.status)
    except thesis_service.ThesisEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return thesis_service.thesis_schema(thesis)


@router.put("/{position_id}/thesis/items/{item_id}", response_model=ThesisSchema)
def set_item_status(
    position_id: int, item_id: str, req: ThesisItemUpdateRequest, session: Session = Depends(get_session)
) -> ThesisSchema:
    thesis = _existing_thesis(session, position_id)
    try:
        thesis_service.set_pillar_status(session, thesis, item_id, req.status)
    except thesis_service.ThesisEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return thesis_service.thesis_schema(thesis)


@router.delete("/{position_id}/thesis/items/{item_id}", response_model=ThesisSchema)
def delete_item(position_id: int, item_id: str, session: Session = Depends(get_session)) -> ThesisSchema:
    thesis = _existing_thesis(session, position_id)
    try:
        thesis_service.remove_item(session, thesis, item_id)
    except thesis_service.ThesisEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return thesis_service.thesis_schema(thesis)
