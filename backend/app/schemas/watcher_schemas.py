from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class WatcherEventSchema(BaseModel):
    id: int
    watcher: str
    symbol: str | None = None
    kind: str
    headline: str
    severity: str
    known_at: UtcDatetime
    source_ref: str | None = None
    details: dict[str, Any] = {}
    # Why the event did nothing beyond being recorded (cooldown, daily cap); None if it fired.
    suppressed_reason: str | None = None


class WatcherStatusSchema(BaseModel):
    name: str
    description: str
    enabled: bool
    poll_interval_seconds: int
    cooldown_seconds: int
    daily_fire_cap: int
    last_run_at: UtcDatetime | None = None
    last_success_at: UtcDatetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    fires_today: int = 0
    recent_events: list[WatcherEventSchema] = []


class WatchersResponse(BaseModel):
    master_enabled: bool
    action: str
    poll_minutes: int
    watchers: list[WatcherStatusSchema]


class WatcherEnabledRequest(BaseModel):
    enabled: bool


class WatcherRunResponse(BaseModel):
    watcher: str
    ran: bool
    skipped_reason: str | None = None
    error: str | None = None
    new_events: int = 0
    duplicates: int = 0
    fired: int = 0
    suppressed: int = 0
    actions: list[str] = []
