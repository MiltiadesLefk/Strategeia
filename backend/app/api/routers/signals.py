from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.analysis.short_volume_scoring import build_short_volume_signal
from app.api.deps import get_app_settings, get_session, require_auth
from app.config import AppSettings
from app.data_providers import universe
from app.data_providers.finra_provider import FinraProvider
from app.knowledge import FactKind, facts_known_as_of
from app.schemas.signals_schemas import FinraDay, FinraRefreshRequest, FinraRefreshResponse, FinraSymbolResponse
from app.schemas.trade_plan_schemas import ShadowSignalOut
from app.signals.finra import refresh_finra_short_volume, reading_is_fresh, short_volume_ratio_as_of

router = APIRouter(prefix="/api/signals", tags=["signals"], dependencies=[Depends(require_auth)])

# A refresh downloads a few public files; not a button to mash. Same in-process
# pattern as the other expensive routes (reset in tests/conftest.py).
FINRA_REFRESH_COOLDOWN_SECONDS = 60
_last_finra_refresh_monotonic: float | None = None
# Days listed back to the caller by the read endpoint.
FINRA_DAYS_SHOWN = 20


def get_finra_provider() -> FinraProvider:
    """The FINRA downloader; a test overrides this dependency."""
    return FinraProvider()


@router.post("/finra/refresh", response_model=FinraRefreshResponse)
def refresh_finra(
    body: FinraRefreshRequest | None = None,
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
    provider: FinraProvider = Depends(get_finra_provider),
) -> FinraRefreshResponse:
    """Download the last few days of FINRA short volume for the watchlist and store it.
    Idempotent: days already stored are not downloaded again."""
    global _last_finra_refresh_monotonic
    now = time.monotonic()
    if _last_finra_refresh_monotonic is not None:
        elapsed = now - _last_finra_refresh_monotonic
        if elapsed < FINRA_REFRESH_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"FINRA short volume was just refreshed {elapsed:.0f}s ago: wait "
                f"{FINRA_REFRESH_COOLDOWN_SECONDS - elapsed:.0f}s before refreshing again.",
            )
    _last_finra_refresh_monotonic = now
    symbols = body.symbols if body and body.symbols else [
        e.symbol for e in universe.load_universe()[: settings.scan_universe_size]
    ]
    result = refresh_finra_short_volume(session, symbols, provider)
    return FinraRefreshResponse(
        symbols=len(set(symbols)),
        days_checked=result.days_checked,
        days_fetched=result.days_fetched,
        days_without_file=result.days_without_file,
        facts_created=result.facts_created,
        facts_existing=result.facts_existing,
        errors=result.errors,
    )


@router.get("/finra/{symbol}", response_model=FinraSymbolResponse)
def get_finra_symbol(
    symbol: str,
    direction: Literal["long", "short"] | None = Query(default=None),
    session: Session = Depends(get_session),
) -> FinraSymbolResponse:
    """What is stored for one symbol and what the silent signal reads from it.
    Read-only: it never downloads or writes."""
    symbol = symbol.strip().upper()
    facts = facts_known_as_of(session, FactKind.FINRA_SHORT_VOLUME, symbol=symbol, limit=FINRA_DAYS_SHOWN)
    reading = short_volume_ratio_as_of(session, symbol)
    signal = build_short_volume_signal(direction, reading, fresh=reading is None or reading_is_fresh(reading))
    days = [
        FinraDay(
            trade_date=(f.effective_at or f.known_at).date().isoformat(),
            short_volume=f.payload["short_volume"],
            total_volume=f.payload["total_volume"],
            ratio=f.payload["ratio"],
        )
        for f in sorted(facts, key=lambda f: f.effective_at or f.known_at, reverse=True)
    ]
    return FinraSymbolResponse(
        symbol=symbol,
        stored_days=len(facts),
        recent_ratio=reading.recent_ratio if reading else None,
        recent_days=reading.recent_days if reading else 0,
        baseline_ratio=reading.baseline_ratio if reading else None,
        baseline_days=reading.baseline_days if reading else 0,
        latest_trade_date=reading.latest_trade_date.isoformat() if reading else None,
        days=days,
        signal=ShadowSignalOut(**signal.to_dict()),
    )
