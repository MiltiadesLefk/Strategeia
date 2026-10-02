"""Tables for the Forecast Lab: trained models and the opinions they gave."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.timeutil import utcnow_naive


class MlModel(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow_naive)
    # Backtest runs the rows came from, as a JSON list of ids.
    run_ids_json: str = "[]"
    feature_names_json: str = "[]"
    # The LightGBM model in its text form (small: a few trees).
    model_text: str = ""
    best_iteration: int = 0
    params_json: str = "{}"
    # Row counts, split dates, metrics with n and intervals, global importance: JSON.
    split_json: str = "{}"
    metrics_json: str = "{}"
    importance_json: str = "[]"
    # Plain-language caveats (price-only features, small samples) shown with the model.
    notes_json: str = "[]"
    active: bool = Field(default=False, index=True)


class MlPrediction(SQLModel, table=True):
    """What the model said about one evaluation of an ML-style sleeve."""

    id: Optional[int] = Field(default=None, primary_key=True)
    plan_id: int = Field(index=True)
    model_id: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow_naive)
    available: bool = True
    expected_r: Optional[float] = None
    min_expected_r: Optional[float] = None
    stopped_trade: bool = False
    # Top SHAP contributions for this decision, as JSON: [{feature, value, shap}].
    top_features_json: str = "[]"
    base_value: Optional[float] = None
    note: Optional[str] = None
