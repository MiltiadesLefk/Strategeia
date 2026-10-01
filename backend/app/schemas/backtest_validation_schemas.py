from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class ValidationCreatedResponse(BaseModel):
    id: int
    status: str


class ValidationProgress(BaseModel):
    runs_done: int
    runs_total: int
    label: str | None = None
    days_done: int = 0
    days_total: int = 0


class ValidationSchema(BaseModel):
    id: int
    status: str  # queued | running | done | failed | cancelled
    created_at: UtcDatetime
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    error: str | None = None
    cancel_requested: bool
    progress: ValidationProgress
    strategy_fingerprint: str | None = None
    # The request as made (symbols, dates, folds, mode, grid, embargo, overrides).
    params: dict[str, Any]


class ValidationDetailSchema(ValidationSchema):
    # folds (in-sample vs out-of-sample numbers), the stitched out-of-sample curve, the
    # aggregate, the deflated Sharpe block. Null until the first fold finishes.
    result: dict[str, Any] | None = None
    # Two checklist lines and the honesty banners; null until the validation is done.
    scorecard: dict[str, Any] | None = None


class KnobSchema(BaseModel):
    name: str
    label: str
    kind: str
    low: float
    high: float
    help: str
    live_value: float


class ValidationOptionsSchema(BaseModel):
    knobs: list[KnobSchema]
    min_folds: int
    max_folds: int
    default_folds: int
    modes: list[str]
    default_mode: str
    default_train_ratio: float
    min_train_ratio: float
    max_train_ratio: float
    default_embargo_days: int
    max_embargo_days: int
    min_window_days: int
    max_values_per_knob: int
    max_variants: int
    max_total_runs: int
