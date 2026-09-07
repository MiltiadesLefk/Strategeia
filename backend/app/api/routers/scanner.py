from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_app_settings, get_data_provider
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.universe import get_default_watchlist, load_universe
from app.schemas.scan_schemas import ScanResponse
from app.services.scanner_service import scan_symbols

router = APIRouter(prefix="/api", tags=["scanner"])


@router.get("/scan", response_model=ScanResponse)
def scan(
    symbols: str | None = Query(None, description="Comma-separated symbols; defaults to the configured watchlist"),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> ScanResponse:
    symbol_list = [s.strip().upper() for s in symbols.split(",")] if symbols else get_default_watchlist(settings.scan_universe_size)
    results, errors = scan_symbols(symbol_list, data_provider)
    return ScanResponse(results=results, errors=errors)


@router.get("/universe")
def universe() -> list[dict]:
    return [entry.__dict__ for entry in load_universe()]
