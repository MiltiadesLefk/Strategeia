from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_app_settings, get_data_provider, require_auth
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.schemas.watchlist_schemas import (
    SymbolCheckRequest,
    SymbolCheckResponse,
    WatchlistResponse,
    WatchlistSaveRequest,
)
from app.services import watchlist_service
from app.services.watchlist_service import WatchlistError

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"], dependencies=[Depends(require_auth)])


@router.get("", response_model=WatchlistResponse)
def get_watchlist(settings: AppSettings = Depends(get_app_settings)) -> WatchlistResponse:
    return watchlist_service.describe_watchlist(settings)


@router.put("", response_model=WatchlistResponse)
def save_watchlist(
    body: WatchlistSaveRequest,
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> WatchlistResponse:
    """Replace the saved watchlist (order is kept: scans take the first N)."""
    try:
        watchlist_service.save_watchlist(body.symbols, data_provider)
    except WatchlistError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return watchlist_service.describe_watchlist(settings)


@router.post("/validate", response_model=SymbolCheckResponse)
def validate_symbol(
    body: SymbolCheckRequest, data_provider: DataProvider = Depends(get_data_provider)
) -> SymbolCheckResponse:
    """Check that a candidate symbol really returns a quote. Always 200; the
    answer is in `valid` and `message`. Saves nothing."""
    return watchlist_service.check_symbol(body.symbol, data_provider)


@router.delete("", response_model=WatchlistResponse)
def reset_watchlist(settings: AppSettings = Depends(get_app_settings)) -> WatchlistResponse:
    """Forget the saved list and go back to the bundled one."""
    watchlist_service.reset_watchlist()
    return watchlist_service.describe_watchlist(settings)
