"""Saved state of each watcher, so cooldowns, caps and backoff survive a restart."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class WatcherState(SQLModel, table=True):
    """One row per watcher name (created the first time the watcher is seen).
    Times are naive UTC like every other column."""

    name: str = Field(primary_key=True)
    # Per-watcher switch, on top of the master Watchers switch in Settings.
    enabled: bool = True
    last_run_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0
    # Fires counted against the daily cap, and the US Eastern date (YYYY-MM-DD) they belong to.
    fires_today: int = 0
    fires_day: Optional[str] = None
    # symbol -> source_ref of the last event that fired for it ("" for no ref);
    # symbol "*" stands for market-wide events.
    last_event_keys: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    # symbol -> ISO time (naive UTC) the last event fired for it: what the cooldown measures from.
    last_fired_at: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
