from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.api.deps import get_app_settings, get_session, require_auth
from app.config import AppSettings
from app.schemas.smart_money_candidate_schemas import SmartMoneyCandidatesResponse
from app.services import smart_money_candidate_service as service

router = APIRouter(
    prefix="/api/smart-money/candidates", tags=["smart-money"], dependencies=[Depends(require_auth)]
)


@router.get("", response_model=SmartMoneyCandidatesResponse)
def get_candidates(
    status: str | None = Query(default=None, max_length=24),
    limit: int = Query(default=service.DEFAULT_LIMIT, ge=1, le=service.MAX_LIMIT),
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
) -> SmartMoneyCandidatesResponse:
    """Smart-money candidates, newest first. Read-only: nothing is scored or fetched here."""
    return service.list_candidates(session, settings, status=status, limit=limit)
