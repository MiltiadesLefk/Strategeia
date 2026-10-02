from __future__ import annotations

from typing import Any

from app.schemas.common import UtcDatetime
from pydantic import BaseModel, Field


class ForecastRunSchema(BaseModel):
    id: int
    finished_at: UtcDatetime | None = None
    closed_trades: int  # closed trades with stored scores, usable for training


class ForecastModelSummary(BaseModel):
    id: int
    created_at: UtcDatetime
    active: bool
    run_ids: list[int]
    n_rows: int
    n_test: int
    verdict: str


class ForecastModelDetail(ForecastModelSummary):
    feature_names: list[str]
    best_iteration: int
    params: dict[str, Any]
    split: dict[str, Any]
    metrics: dict[str, Any]
    importance: list[dict[str, Any]]
    notes: list[str]


class ForecastStatus(BaseModel):
    extras_available: bool
    extras_missing: list[str]
    extras_message: str
    enabled: bool
    min_expected_r: float
    min_rows: int
    ml_sleeve_key: str | None = None
    active_model: ForecastModelSummary | None = None
    runs: list[ForecastRunSchema]


class TrainRequest(BaseModel):
    run_ids: list[int] = Field(min_length=1, max_length=20)


class ForecastPrediction(BaseModel):
    id: int
    plan_id: int
    symbol: str | None = None
    created_at: UtcDatetime
    model_id: int | None = None
    available: bool
    expected_r: float | None = None
    min_expected_r: float | None = None
    stopped_trade: bool
    top_features: list[dict[str, Any]]
    base_value: float | None = None
    note: str | None = None
