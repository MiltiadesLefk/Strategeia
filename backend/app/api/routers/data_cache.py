from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_auth
from app.data_providers.cache import cache_stats, clear_cache
from app.data_providers.history_store import peek_history_summary
from app.schemas.cache_schemas import (
    CacheClearResponse,
    CacheStatusResponse,
    HistoryStoreStatus,
    PersistentCacheStatus,
    StaleSymbol,
)

router = APIRouter(prefix="/api/cache", tags=["cache"], dependencies=[Depends(require_auth)])

# Clearing sends the next scan back to the data providers for everything, which
# is exactly what rate limits punish, so it is not a button to mash. Same shape
# as the other expensive routes (see scanner.py's auto-trade cooldown).
CACHE_CLEAR_COOLDOWN_SECONDS = 30
_last_cache_clear_monotonic: float | None = None


def _from_epoch(value: float | None) -> datetime | None:
    return datetime.fromtimestamp(value, tz=timezone.utc) if value is not None else None


@router.get("/status", response_model=CacheStatusResponse)
def cache_status() -> CacheStatusResponse:
    """Read-only numbers for the Settings page's data-cache card. Never writes."""
    stats = cache_stats()
    persistent = stats.persistent
    history = peek_history_summary()
    return CacheStatusResponse(
        memory_entries=stats.memory_entries,
        memory_stale_entries=stats.memory_stale_entries,
        counters=stats.counters,
        hit_rate=stats.hit_rate,
        persistent=(
            PersistentCacheStatus(
                file_name=Path(persistent.path).name,
                entries=persistent.entries,
                fresh_entries=persistent.fresh_entries,
                payload_bytes=persistent.payload_bytes,
                file_bytes=persistent.file_bytes,
                max_bytes=persistent.max_bytes,
                oldest_stored_at=_from_epoch(persistent.oldest_stored_at),
                newest_stored_at=_from_epoch(persistent.newest_stored_at),
                pending_writes=persistent.pending_writes,
                errors=persistent.errors,
                skipped_unencodable=persistent.skipped_unencodable,
            )
            if persistent is not None and persistent.enabled
            else None
        ),
        history=HistoryStoreStatus(
            symbols=history.symbols,
            bars=history.bars,
            first_date=history.first_date,
            last_date=history.last_date,
            file_bytes=history.file_bytes,
            stalest=[StaleSymbol(symbol=s, last_date=d) for s, d in history.stalest],
        ),
    )


@router.post("/clear", response_model=CacheClearResponse)
def clear_provider_cache() -> CacheClearResponse:
    """Empty the provider cache, memory and disk. Does NOT touch the
    price-history store: that is data you chose to keep."""
    global _last_cache_clear_monotonic
    now = time.monotonic()
    if _last_cache_clear_monotonic is not None:
        elapsed = now - _last_cache_clear_monotonic
        if elapsed < CACHE_CLEAR_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"The cache was just cleared {elapsed:.0f}s ago: wait "
                f"{CACHE_CLEAR_COOLDOWN_SECONDS - elapsed:.0f}s before clearing it again.",
            )
    _last_cache_clear_monotonic = now
    clear_cache()
    return CacheClearResponse(cleared=True, message="Provider cache cleared. The next requests refetch from the data providers.")
