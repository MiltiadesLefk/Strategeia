from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.api.deps import get_app_settings, get_data_provider, get_llm_provider, get_session
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.universe import get_default_watchlist, load_universe
from app.llm_providers.base import LLMProvider
from app.schemas.scan_schemas import AutoScanResponse, ScanResponse
from app.services.automation_service import run_auto_scan
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


@router.post("/scan/auto-trade", response_model=AutoScanResponse)
def run_auto_trade_now(
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    settings: AppSettings = Depends(get_app_settings),
    session: Session = Depends(get_session),
) -> AutoScanResponse:
    """Manual trigger for the same scan -> generate -> execute pipeline the
    scheduler runs 3x/day (Asia/London/New York session opens) — runs
    immediately regardless of whether the unattended `auto_scan_enabled`
    toggle is on, same relationship "Rescan" has to the read-only scan
    endpoint above."""
    outcome = run_auto_scan(settings, data_provider, llm_provider, session)
    return AutoScanResponse(generated=outcome.generated, no_trade=outcome.no_trade)
