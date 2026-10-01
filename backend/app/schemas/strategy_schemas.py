from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class StrategyVersionSchema(BaseModel):
    id: int
    number: int
    fingerprint: str
    created_at: UtcDatetime
    label: str | None = None
    # The full decision-relevant snapshot: {"settings": {...}, "rules": {...}}
    # plus "overlay" while the AI Trading Overlay is on.
    settings_snapshot: dict[str, Any]
    # What changed from the previous version, one plain-English line each
    # ("min confidence 30 -> 38"). Empty for the first version.
    changes: list[str]
    # Counts of what was made under this version.
    plans: int
    no_trades: int
    positions_opened: int
    closed_trades: int


class StrategyHistoryResponse(BaseModel):
    versions: list[StrategyVersionSchema]  # newest first
    # Plans made before versioning existed; they carry no version number.
    unversioned_plans: int
    # The version the current settings and code map to, or None while they
    # have not produced a plan yet (a version is only recorded when a plan is
    # generated under it).
    current_number: int | None
