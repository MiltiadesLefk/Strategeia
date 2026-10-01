from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class PersistentCacheStatus(BaseModel):
    """The on-disk half of the provider cache (runtime/cache.db)."""

    file_name: str
    entries: int
    # Rows whose time-to-live has not run out. The rest are kept only as
    # last-known-good, served when a provider fails.
    fresh_entries: int
    payload_bytes: int
    file_bytes: int
    max_bytes: int
    oldest_stored_at: UtcDatetime | None = None
    newest_stored_at: UtcDatetime | None = None
    # Writes waiting for the background writer, and problems met so far
    # (a disk error never breaks a data call, it is only counted here).
    pending_writes: int
    errors: int
    # Results the safe codec can't carry; they stay memory-only.
    skipped_unencodable: int


class StaleSymbol(BaseModel):
    symbol: str
    last_date: date


class HistoryStoreStatus(BaseModel):
    """The price-history store (runtime/history.db): daily bars kept for research."""

    symbols: int
    bars: int
    first_date: date | None = None
    last_date: date | None = None
    file_bytes: int
    # The symbols whose newest stored bar is oldest: where a refresh is due.
    stalest: list[StaleSymbol]


class CacheStatusResponse(BaseModel):
    memory_entries: int
    # Last-known-good values held in memory for stale-on-error.
    memory_stale_entries: int
    # Since this process started: memory_hits, disk_hits, coalesced_hits,
    # misses, stale_served, stale_served_from_disk, fetch_failures.
    counters: dict[str, int]
    # (memory + disk + coalesced hits) / all lookups; None before the first one.
    hit_rate: float | None = None
    # None when persistence is switched off (PERSIST_CACHE_DB=false).
    persistent: PersistentCacheStatus | None = None
    history: HistoryStoreStatus


class CacheClearResponse(BaseModel):
    cleared: bool
    message: str
