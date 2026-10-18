from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.deps import get_app_settings, get_session, require_auth
from app.config import AppSettings
from app.schemas.smart_money_schemas import (
    InsiderClustersResponse,
    InsiderRefreshRequest,
    InsiderRefreshResponse,
    InsiderStatusResponse,
    InsiderSummaryResponse,
    InsiderTradesResponse,
)
from app.services import smart_money_service as service
from app.services.smart_money_overview_service import overview as build_overview
from app.smartmoney.tracker import smart_money_score_as_of

router = APIRouter(prefix="/api/smart-money", tags=["smart-money"], dependencies=[Depends(require_auth)])

# A refresh asks SEC for filings, one request each; not a button to mash. Same
# in-process pattern as the other expensive routes (reset in tests/conftest.py).
INSIDER_REFRESH_COOLDOWN_SECONDS = 60
_last_insider_refresh_monotonic: float | None = None

DaysQuery = Query(default=service.DEFAULT_DAYS, ge=1, le=service.MAX_DAYS)


@router.get("/status", response_model=InsiderStatusResponse)
def get_status(session: Session = Depends(get_session)) -> InsiderStatusResponse:
    """What is stored. Read-only; an empty database answers has_data=false."""
    return service.status(session)


@router.get("/insiders", response_model=InsiderTradesResponse)
def get_insider_trades(
    days: int = DaysQuery,
    symbol: str | None = Query(default=None, max_length=12),
    min_value: float = Query(default=0.0, ge=0),
    side: Literal["buys", "sells", "all"] = "all",
    session: Session = Depends(get_session),
) -> InsiderTradesResponse:
    """Open-market insider buys and sells from stored Form 4 filings, newest filing first."""
    return service.list_trades(session, days=days, symbol=symbol, min_value=min_value, side=side)


@router.get("/insiders/clusters", response_model=InsiderClustersResponse)
def get_insider_clusters(
    days: int = DaysQuery,
    symbol: str | None = Query(default=None, max_length=12),
    session: Session = Depends(get_session),
) -> InsiderClustersResponse:
    return service.list_clusters(session, days=days, symbol=symbol)


@router.get("/insiders/summary/{symbol}", response_model=InsiderSummaryResponse)
def get_insider_summary(
    symbol: str, days: int = DaysQuery, session: Session = Depends(get_session)
) -> InsiderSummaryResponse:
    return service.symbol_summary(session, symbol, days=days)


@router.post("/insiders/refresh", response_model=InsiderRefreshResponse)
def refresh_insiders(
    body: InsiderRefreshRequest | None = None,
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
) -> InsiderRefreshResponse:
    """Load Form 4 filings for the watchlist (or the given symbols) from SEC EDGAR.
    A few symbols per call; call again while `symbols_remaining` is above zero."""
    global _last_insider_refresh_monotonic
    now = time.monotonic()
    if _last_insider_refresh_monotonic is not None:
        elapsed = now - _last_insider_refresh_monotonic
        if elapsed < INSIDER_REFRESH_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"Insider filings were just refreshed {elapsed:.0f}s ago: wait "
                f"{INSIDER_REFRESH_COOLDOWN_SECONDS - elapsed:.0f}s before refreshing again.",
            )
    _last_insider_refresh_monotonic = now
    symbols = body.symbols if body and body.symbols else service.default_refresh_symbols(settings.scan_universe_size)
    return service.refresh_insiders(session, symbols)


@router.get("/score/{symbol}")
def get_smart_money_score(
    symbol: str, session: Session = Depends(get_session), settings: AppSettings = Depends(get_app_settings)
) -> dict:
    """All smart-money sources of one stock in one score (insiders, Congress, 13F funds, 5% owners, 8-Ks), with the
    points and the reason of each part. The same scorer the morning scan uses; read-only."""
    scored = smart_money_score_as_of(session, symbol.strip().upper(), None, settings)
    return {
        "symbol": scored.symbol,
        "total": scored.result.total,
        "min_score": settings.smart_money_min_score,
        "components": [{"source": c.source, "points": c.points, "reason": c.reason} for c in scored.result.components],
    }


@router.get("/overview")
def get_smart_money_overview(
    days: int = DaysQuery, limit: int = Query(30, ge=1, le=100),
    session: Session = Depends(get_session), settings: AppSettings = Depends(get_app_settings),
) -> dict:
    """Totals of every source (insiders, Congress, funds, 5% owners, candidates) and the stocks ranked by combined score."""
    return build_overview(session, settings, days, limit)
