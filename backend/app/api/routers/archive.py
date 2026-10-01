from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.api.deps import get_session, require_auth
from app.schemas.archive_schemas import ArchiveResponse
from app.services.archive_service import get_archive_summary

router = APIRouter(prefix="/api/archive", tags=["archive"], dependencies=[Depends(require_auth)])


@router.get("/{symbol}", response_model=ArchiveResponse)
def archive_for_symbol(
    symbol: str,
    limit: int = Query(5, ge=1, le=50, description="How many recent items of each kind to include"),
    session: Session = Depends(get_session),
) -> ArchiveResponse:
    """What the dated archive holds for one symbol, plus its overall size.
    Read-only: this never fetches data and never writes."""
    return get_archive_summary(session, symbol.upper(), limit)
