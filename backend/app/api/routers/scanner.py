from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.deps import get_app_settings, get_data_provider, get_llm_provider, get_session, require_auth
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.universe import get_default_watchlist, load_universe
from app.llm_providers.base import LLMProvider
from app.schemas.scan_schemas import AutoScanResponse, ScanResponse
from app.services.automation_service import run_auto_scan
from app.services.scanner_service import scan_symbols

router = APIRouter(prefix="/api", tags=["scanner"], dependencies=[Depends(require_auth)])

# A single request otherwise had no cap on how many symbols it could force
# scan_symbols() to fetch — one unauthenticated GET with a few thousand
# comma-separated junk symbols could trigger a few thousand sequential
# provider calls. This is generous enough to never bind the real watchlist
# (scan_universe_size tops out far below it) while still bounding worst case.
MAX_SCAN_SYMBOLS = 200


@router.get("/scan", response_model=ScanResponse)
def scan(
    symbols: str | None = Query(None, description="Comma-separated symbols; defaults to the configured watchlist"),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> ScanResponse:
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else get_default_watchlist(settings.scan_universe_size)
    if len(symbol_list) > MAX_SCAN_SYMBOLS:
        raise HTTPException(status_code=400, detail=f"Too many symbols requested (max {MAX_SCAN_SYMBOLS}).")
    results, errors = scan_symbols(symbol_list, data_provider)
    return ScanResponse(results=results, errors=errors)


@router.get("/universe")
def universe() -> list[dict]:
    return [entry.__dict__ for entry in load_universe()]


AUTO_TRADE_COOLDOWN_SECONDS = 60
_last_auto_trade_run_monotonic: float | None = None


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
    endpoint above.

    This is the single biggest cost/exposure amplifier in the API: one call
    fans out across the whole scan universe (default 50 symbols), calling
    the configured LLM/data providers per symbol and — since
    auto_execute_trade_plans defaults on — potentially opening a real paper
    position per qualifying symbol. A short in-process cooldown (not
    persisted — process-lifetime only, same pattern as data_providers/cache.py)
    stops it from being hammered in a tight loop, independent of whether
    require_auth's opt-in auth is configured."""
    global _last_auto_trade_run_monotonic
    now = time.monotonic()
    if _last_auto_trade_run_monotonic is not None:
        elapsed = now - _last_auto_trade_run_monotonic
        if elapsed < AUTO_TRADE_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"Auto-trade was just run {elapsed:.0f}s ago — wait "
                f"{AUTO_TRADE_COOLDOWN_SECONDS - elapsed:.0f}s before running it again.",
            )
    _last_auto_trade_run_monotonic = now

    outcome = run_auto_scan(settings, data_provider, llm_provider, session)
    return AutoScanResponse(generated=outcome.generated, no_trade=outcome.no_trade)
