from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.deps import get_app_settings, get_session, require_auth
from app.config import AppSettings
from app.portfolio.missed_trade_report import build_missed_trade_report
from app.portfolio.missed_trades import BarsLoader, history_store_bars_loader, refresh_missed_trades
from app.schemas.missed_trade_schemas import MissedTradeRefreshSchema, MissedTradeReportSchema

router = APIRouter(prefix="/api/missed-trades", tags=["missed-trades"], dependencies=[Depends(require_auth)])

# A refresh reads price history for every symbol with something to compute, which
# can be a download per symbol: not a button to mash. Same shape as the other
# expensive routes (see data_cache.py's clear cooldown).
MISSED_TRADES_REFRESH_COOLDOWN_SECONDS = 30
_last_missed_trades_refresh_monotonic: float | None = None


def get_missed_trade_bars_loader() -> BarsLoader:
    """The price-history source for a refresh; a test overrides this dependency."""
    return history_store_bars_loader


@router.get("", response_model=MissedTradeReportSchema)
def missed_trades_report(session: Session = Depends(get_session)) -> MissedTradeReportSchema:
    """What the trades we did not take would have earned. Read-only: it reads the
    stored outcomes and never computes, fetches prices or writes, so opening the
    page cannot change the report. POST /refresh does the computing."""
    return MissedTradeReportSchema.model_validate(build_missed_trade_report(session))


@router.post("/refresh", response_model=MissedTradeRefreshSchema)
def refresh_missed_trade_outcomes(
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
    load_bars: BarsLoader = Depends(get_missed_trade_bars_loader),
) -> MissedTradeRefreshSchema:
    """Compute the outcomes that are new or still open (a bounded number per call).
    Results that reached a stop, a target or the time limit are final and are
    never recomputed."""
    global _last_missed_trades_refresh_monotonic
    now = time.monotonic()
    if _last_missed_trades_refresh_monotonic is not None:
        elapsed = now - _last_missed_trades_refresh_monotonic
        if elapsed < MISSED_TRADES_REFRESH_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"Missed trades were just refreshed {elapsed:.0f}s ago: wait "
                f"{MISSED_TRADES_REFRESH_COOLDOWN_SECONDS - elapsed:.0f}s before refreshing again.",
            )
    _last_missed_trades_refresh_monotonic = now
    return MissedTradeRefreshSchema.model_validate(refresh_missed_trades(session, settings, load_bars))
