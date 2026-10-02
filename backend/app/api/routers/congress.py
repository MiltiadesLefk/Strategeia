from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.deps import get_app_settings, get_session, require_auth
from app.config import AppSettings
from app.data_providers.base import DataProviderError
from app.schemas.congress_schemas import (
    CongressClustersResponse,
    CongressMemberNamesResponse,
    CongressMembersResponse,
    CongressRefreshResponse,
    CongressStatusResponse,
    CongressTradesResponse,
)
from app.services import congress_service as service

router = APIRouter(
    prefix="/api/smart-money/congress", tags=["smart-money"], dependencies=[Depends(require_auth)]
)

# A refresh downloads PDFs from the House Clerk's site, one request each; not a
# button to mash. Same in-process pattern as the other expensive routes (reset in
# tests/conftest.py).
CONGRESS_REFRESH_COOLDOWN_SECONDS = 60
_last_congress_refresh_monotonic: float | None = None

DaysQuery = Query(default=service.DEFAULT_DAYS, ge=1, le=service.MAX_DAYS)


@router.get("/status", response_model=CongressStatusResponse)
def get_status(
    session: Session = Depends(get_session), settings: AppSettings = Depends(get_app_settings)
) -> CongressStatusResponse:
    """What is stored. Read-only; an empty database answers has_data=false."""
    return service.status(session, settings)


@router.get("/trades", response_model=CongressTradesResponse)
def get_trades(
    days: int = DaysQuery,
    symbol: str | None = Query(default=None, max_length=12),
    side: Literal["buys", "sells", "all"] = "all",
    member: str | None = Query(default=None, max_length=80),
    followed_only: bool = False,
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
) -> CongressTradesResponse:
    """Trades from stored House reports, newest filing first. `followed_only`
    applies the Settings follow list (when it is in "list" mode)."""
    return service.list_trades(
        session, settings, days=days, symbol=symbol, side=side, member=member, followed_only=followed_only
    )


@router.get("/clusters", response_model=CongressClustersResponse)
def get_clusters(
    days: int = DaysQuery,
    symbol: str | None = Query(default=None, max_length=12),
    followed_only: bool = False,
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
) -> CongressClustersResponse:
    return service.list_clusters(session, settings, days=days, symbol=symbol, followed_only=followed_only)


@router.get("/members", response_model=CongressMembersResponse)
def get_members(
    days: int = DaysQuery,
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
) -> CongressMembersResponse:
    return service.list_members(session, settings, days=days)


@router.get("/member-names", response_model=CongressMemberNamesResponse)
def get_member_names(
    q: str = Query(default="", max_length=80),
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
) -> CongressMemberNamesResponse:
    """Member names seen in stored reports, for the "who to follow" search."""
    return service.member_names(session, settings, q)


@router.post("/refresh", response_model=CongressRefreshResponse)
def refresh(session: Session = Depends(get_session)) -> CongressRefreshResponse:
    """Load the newest House trade reports from the Clerk's site. A few reports per
    call; call again while `filings_remaining` is above zero."""
    global _last_congress_refresh_monotonic
    now = time.monotonic()
    if _last_congress_refresh_monotonic is not None:
        elapsed = now - _last_congress_refresh_monotonic
        if elapsed < CONGRESS_REFRESH_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"Congress reports were just refreshed {elapsed:.0f}s ago: wait "
                f"{CONGRESS_REFRESH_COOLDOWN_SECONDS - elapsed:.0f}s before refreshing again.",
            )
    _last_congress_refresh_monotonic = now
    try:
        return service.refresh_congress(session)
    except DataProviderError as exc:
        raise HTTPException(status_code=502, detail=f"The House Clerk's site could not be read: {exc}") from exc
