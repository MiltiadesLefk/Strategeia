from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class DataSourceHealth(BaseModel):
    """One data source with its live numbers. Every number is computed from
    calls this process actually made; a source never called has none."""

    name: str
    label: str
    purpose: str
    # True for the configured per-symbol chain (position is its place in the
    # fallback order, 1 = tried first); false for the other sources.
    in_chain: bool
    position: int | None = None
    # healthy | degraded | failing | unused
    status: str
    # Lifetime counters since the process started.
    calls: int
    successes: int
    failures: int
    # Over the rolling window (the last `window_size` calls).
    window_calls: int
    success_rate: float | None = None
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    consecutive_failures: int
    last_used_at: UtcDatetime | None = None
    last_success_at: UtcDatetime | None = None
    last_error: str | None = None
    last_error_at: UtcDatetime | None = None
    # Share of cache lookups answered without calling the source (None: no lookups).
    cache_hit_ratio: float | None = None
    # Times old cached data was served because the live call failed.
    stale_served: int
    can_probe: bool


class DataSourcesResponse(BaseModel):
    chain: list[DataSourceHealth]
    others: list[DataSourceHealth]
    window_size: int
    generated_at: UtcDatetime


class ProbeResponse(BaseModel):
    name: str
    ok: bool
    latency_ms: float
    detail: str | None = None
    error: str | None = None
