from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UtcDatetime


class ReplayRequest(BaseModel):
    # Free-form on purpose: the replay itself names which settings it can and
    # cannot recompute, and explains each refusal, instead of a bare "extra field".
    overrides: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class _FromAttributes(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ReplayStatsSchema(_FromAttributes):
    taken: int
    resolved: int
    open: int
    no_result: int
    wins: int
    win_rate: float | None
    win_rate_low: float | None
    win_rate_high: float | None
    avg_r: float | None
    avg_r_low: float | None
    avg_r_high: float | None
    total_r: float
    small_sample: bool


class ReplayFlipSchema(_FromAttributes):
    plan_id: int
    symbol: str
    created_at: UtcDatetime
    direction: str | None
    flip: str
    why: str
    confidence_points: int | None
    points_after: int | None
    result_state: str
    result_r: float | None
    strategy_version: int | None


class ReplayVersionSchema(_FromAttributes):
    strategy_version: int | None
    decisions: int


class ReplayResultSchema(_FromAttributes):
    overrides: dict[str, Any]
    decisions: int
    truncated: bool
    before: ReplayStatsSchema
    after: ReplayStatsSchema
    flipped_count: int
    now_taken: int
    now_skipped: int
    flips_without_result: int
    flips: list[ReplayFlipSchema]
    fixed_count: int
    baseline_disagrees: int
    versions: list[ReplayVersionSchema]
    replayable: dict[str, str]
    caveats: list[str]
