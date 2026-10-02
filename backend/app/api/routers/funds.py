"""Smart Money: fund 13F holdings and 5% owner filings. Thin: the logic is in
`app.services.funds_service`. Every GET is read-only; only the refresh writes."""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import get_app_settings, get_session, require_auth
from app.config import AppSettings
from app.schemas.funds_schemas import (
    FundChangesResponse,
    FundHoldersResponse,
    FundsOverviewResponse,
    FundsRefreshResponse,
    OwnershipResponse,
)
from app.services import funds_service as service

router = APIRouter(prefix="/api/smart-money", tags=["smart-money-funds"], dependencies=[Depends(require_auth)])

# A refresh asks SEC for filings, several requests each; not a button to mash.
# Same in-process pattern as the other expensive routes (reset in tests/conftest.py).
_last_funds_refresh_monotonic: float | None = None

CikPath = Path(pattern=r"^[0-9]{1,10}$")


class FundsRefreshRequest(BaseModel):
    # Company tickers whose 13D/13G filings to look up (default: the start of the watchlist).
    symbols: list[str] | None = Field(default=None, max_length=service.REFRESH_OWNERSHIP_SYMBOLS)


@router.get("/funds", response_model=FundsOverviewResponse)
def get_funds(
    session: Session = Depends(get_session), settings: AppSettings = Depends(get_app_settings)
) -> FundsOverviewResponse:
    """The followed funds with their newest stored quarter. Read-only."""
    return service.overview(session, settings)


@router.get("/funds/holders/{symbol}", response_model=FundHoldersResponse)
def get_fund_holders(
    symbol: str = Path(max_length=12),
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
) -> FundHoldersResponse:
    """Which followed funds hold this symbol at their latest known quarter end."""
    return service.holders_of(session, settings, symbol)


@router.get("/funds/{cik}/changes", response_model=FundChangesResponse)
def get_fund_changes(
    cik: str = CikPath,
    status: Literal["all", "new", "added", "trimmed", "sold_out", "unchanged"] = "all",
    session: Session = Depends(get_session),
) -> FundChangesResponse:
    """New, added, trimmed and sold-out positions: the newest stored quarter against the one before."""
    return service.fund_changes(session, cik, status=status)


@router.get("/ownership", response_model=OwnershipResponse)
def get_ownership(
    days: int = Query(default=service.DEFAULT_OWNERSHIP_DAYS, ge=1, le=service.MAX_OWNERSHIP_DAYS),
    schedule: Literal["all", "13D", "13G"] = "all",
    symbol: str | None = Query(default=None, max_length=12),
    session: Session = Depends(get_session),
) -> OwnershipResponse:
    """Schedule 13D/13G filings (holders over 5%), newest first."""
    return service.ownership(session, days=days, schedule=schedule, symbol=symbol)


@router.post("/funds/refresh", response_model=FundsRefreshResponse)
def refresh_funds(
    body: FundsRefreshRequest | None = None,
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
) -> FundsRefreshResponse:
    """Load 13F reports (followed funds) and 13D/13G filings from SEC EDGAR."""
    global _last_funds_refresh_monotonic
    now = time.monotonic()
    if _last_funds_refresh_monotonic is not None:
        elapsed = now - _last_funds_refresh_monotonic
        if elapsed < service.FUNDS_REFRESH_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"Fund filings were just refreshed {elapsed:.0f}s ago: wait "
                f"{service.FUNDS_REFRESH_COOLDOWN_SECONDS - elapsed:.0f}s before refreshing again.",
            )
    _last_funds_refresh_monotonic = now
    return service.refresh_funds(session, settings, body.symbols if body else None)
