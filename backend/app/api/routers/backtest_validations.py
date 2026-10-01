"""Walk-forward validations: start one, list them, read one. Registered before the
backtests router in main.py: that one has /api/backtests/{run_id}, which would
otherwise try to read "validations" as a run id."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.api.deps import get_app_settings, get_session, require_auth
from app.api.routers.backtests import get_manager
from app.backtest import service, validation
from app.backtest.params import BacktestInputError
from app.backtest.service import BacktestBusyError, BacktestManager, BacktestNotFoundError
from app.backtest.validation import KNOBS, ValidationParams
from app.backtest.validation_models import BacktestValidation
from app.config import AppSettings
from app.schemas.backtest_validation_schemas import (
    KnobSchema,
    ValidationCreatedResponse,
    ValidationDetailSchema,
    ValidationOptionsSchema,
    ValidationProgress,
    ValidationSchema,
)

router = APIRouter(prefix="/api/backtests", tags=["backtest-validation"], dependencies=[Depends(require_auth)])


def _base_fields(row: BacktestValidation) -> dict:
    return dict(
        id=row.id, status=row.status, created_at=row.created_at, started_at=row.started_at,
        finished_at=row.finished_at, error=row.error, cancel_requested=row.cancel_requested,
        progress=ValidationProgress(
            runs_done=row.progress_runs_done, runs_total=row.progress_runs_total, label=row.progress_label,
            days_done=row.progress_days_done, days_total=row.progress_days_total,
        ),
        strategy_fingerprint=row.strategy_fingerprint, params=json.loads(row.params_json or "{}"),
    )


@router.post("/validate", response_model=ValidationCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
def start_validation(
    request: ValidationParams,
    manager: BacktestManager = Depends(get_manager),
    settings: AppSettings = Depends(get_app_settings),
) -> ValidationCreatedResponse:
    """Queue a walk-forward validation and return its id at once; poll GET /api/backtests/validations/{id}.
    Reads price history from disk only, never the network."""
    try:
        validation_id = manager.start_validation(request, settings)
    except BacktestBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (BacktestInputError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ValidationCreatedResponse(id=validation_id, status="queued")


@router.get("/validation-options", response_model=ValidationOptionsSchema)
def get_validation_options(settings: AppSettings = Depends(get_app_settings)) -> ValidationOptionsSchema:
    """What a validation request may contain: the settings a sweep may vary (with your live value
    for each), and the limits. Read-only."""
    return ValidationOptionsSchema(
        knobs=[
            KnobSchema(**vars(knob), live_value=float(getattr(settings, name))) for name, knob in KNOBS.items()
        ],
        min_folds=validation.MIN_FOLDS, max_folds=validation.MAX_FOLDS, default_folds=validation.DEFAULT_FOLDS,
        modes=[validation.MODE_ROLLING, validation.MODE_ANCHORED], default_mode=validation.MODE_ANCHORED,
        default_train_ratio=validation.DEFAULT_TRAIN_RATIO, min_train_ratio=validation.MIN_TRAIN_RATIO,
        max_train_ratio=validation.MAX_TRAIN_RATIO, default_embargo_days=validation.DEFAULT_EMBARGO_DAYS,
        max_embargo_days=validation.MAX_EMBARGO_DAYS, min_window_days=validation.MIN_WINDOW_DAYS,
        max_values_per_knob=validation.MAX_VALUES_PER_KNOB, max_variants=validation.MAX_VARIANTS,
        max_total_runs=validation.MAX_TOTAL_RUNS,
    )


@router.get("/validations", response_model=list[ValidationSchema])
def list_validations(limit: int = 50, session: Session = Depends(get_session)) -> list[ValidationSchema]:
    return [ValidationSchema(**_base_fields(r)) for r in service.list_validations(session, max(1, min(limit, 200)))]


@router.get("/validations/{validation_id}", response_model=ValidationDetailSchema)
def get_validation(validation_id: int, session: Session = Depends(get_session)) -> ValidationDetailSchema:
    try:
        row = service.get_validation(session, validation_id)
    except BacktestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    result = json.loads(row.result_json) if row.result_json else None
    scorecard = validation.build_validation_scorecard(result) if result and result.get("status") == "done" else None
    return ValidationDetailSchema(**_base_fields(row), result=result, scorecard=scorecard)


@router.post("/validations/{validation_id}/cancel")
def cancel_validation(validation_id: int, manager: BacktestManager = Depends(get_manager)) -> dict:
    try:
        return {"id": validation_id, "status": manager.cancel_validation(validation_id)}
    except BacktestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
