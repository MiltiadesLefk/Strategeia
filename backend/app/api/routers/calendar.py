from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.deps import get_data_provider, get_session, require_auth
from app.data_providers.base import DataProvider
from app.schemas.calendar_schemas import CalendarResponse
from app.services.calendar_service import CalendarRangeError, build_calendar

router = APIRouter(prefix="/api/calendar", tags=["calendar"], dependencies=[Depends(require_auth)])


@router.get("", response_model=CalendarResponse)
def calendar(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    symbols: str | None = Query(default=None, description="Comma-separated tickers to add to the earnings rows"),
    data_provider: DataProvider = Depends(get_data_provider),
    session: Session = Depends(get_session),
) -> CalendarResponse:
    # Read-only: the session is only used to look up open positions. Every upstream
    # (the economic feed, earnings dates) is cached by its own client.
    wanted = [s.strip().upper() for s in (symbols or "").split(",") if s.strip()]
    try:
        return build_calendar(session, data_provider, from_date=from_, to_date=to, symbols=wanted)
    except CalendarRangeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
