"""Forecast Lab: train a model on stored backtest trades (only on request), look at
its out-of-sample results and SHAP drivers, activate it, and see what it said.
Thin router: logic lives in app/ml/."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import get_app_settings, get_session, require_auth
from app.backtest.models import RUN_DONE, BacktestRun, BacktestTrade
from app.config import AppSettings
from app.ml.models import MlModel, MlPrediction
from app.ml.runtime import MlExtrasMissing, extras_status
from app.ml.service import (
    MIN_ROWS,
    ML_SLEEVE_NAME,
    ML_SLEEVE_STYLE,
    MlTrainingError,
    active_model,
    set_active,
    train_model,
)
from app.portfolio.models import Sleeve, TradePlanRecord
from app.portfolio.sleeves import SleeveError, create_sleeve
from app.schemas.forecast_schemas import (
    ForecastModelDetail,
    ForecastModelSummary,
    ForecastPrediction,
    ForecastRunSchema,
    ForecastStatus,
    TrainRequest,
)
from app.schemas.sleeve_schemas import SleeveSchema

router = APIRouter(prefix="/api/forecast-lab", tags=["forecast-lab"], dependencies=[Depends(require_auth)])


def _summary(m: MlModel) -> ForecastModelSummary:
    split = json.loads(m.split_json or "{}")
    metrics = json.loads(m.metrics_json or "{}")
    return ForecastModelSummary(
        id=m.id,
        created_at=m.created_at,
        active=m.active,
        run_ids=json.loads(m.run_ids_json or "[]"),
        n_rows=split.get("n_rows", 0),
        n_test=split.get("n_test", 0),
        verdict=metrics.get("verdict", "not_enough_data"),
    )


def _detail(m: MlModel) -> ForecastModelDetail:
    return ForecastModelDetail(
        **_summary(m).model_dump(),
        feature_names=json.loads(m.feature_names_json or "[]"),
        best_iteration=m.best_iteration,
        params=json.loads(m.params_json or "{}"),
        split=json.loads(m.split_json or "{}"),
        metrics=json.loads(m.metrics_json or "{}"),
        importance=json.loads(m.importance_json or "[]"),
        notes=json.loads(m.notes_json or "[]"),
    )


@router.get("/status", response_model=ForecastStatus)
def status(session: Session = Depends(get_session), settings: AppSettings = Depends(get_app_settings)) -> ForecastStatus:
    ex = extras_status()
    counts = dict(
        session.exec(
            select(BacktestTrade.run_id, func.count())
            .where(BacktestTrade.status == "closed", BacktestTrade.scores_json.is_not(None))  # type: ignore[union-attr]
            .group_by(BacktestTrade.run_id)
        ).all()
    )
    runs = session.exec(select(BacktestRun).where(BacktestRun.status == RUN_DONE).order_by(BacktestRun.id.desc()).limit(50)).all()
    sleeve = session.exec(select(Sleeve).where(Sleeve.style == ML_SLEEVE_STYLE)).first()
    model = active_model(session)
    return ForecastStatus(
        extras_available=ex.available,
        extras_missing=list(ex.missing),
        extras_message=ex.message,
        enabled=settings.ml_style_enabled,
        min_expected_r=settings.ml_min_expected_r,
        min_rows=MIN_ROWS,
        ml_sleeve_key=sleeve.key if sleeve else None,
        active_model=_summary(model) if model else None,
        runs=[ForecastRunSchema(id=r.id, finished_at=r.finished_at, closed_trades=counts.get(r.id, 0)) for r in runs],
    )


@router.get("/models", response_model=list[ForecastModelSummary])
def list_models(session: Session = Depends(get_session)) -> list[ForecastModelSummary]:
    return [_summary(m) for m in session.exec(select(MlModel).order_by(MlModel.id.desc()).limit(50)).all()]


@router.get("/models/{model_id}", response_model=ForecastModelDetail)
def get_model(model_id: int, session: Session = Depends(get_session)) -> ForecastModelDetail:
    m = session.get(MlModel, model_id)
    if m is None:
        raise HTTPException(404, "Model not found")
    return _detail(m)


@router.post("/models/train", response_model=ForecastModelDetail, status_code=201)
def train(
    req: TrainRequest, session: Session = Depends(get_session), settings: AppSettings = Depends(get_app_settings)
) -> ForecastModelDetail:
    """Explicit training from the chosen finished backtests. Reads stored rows only
    (no market data, no network); the new model is saved but NOT activated."""
    done = {r.id for r in session.exec(select(BacktestRun).where(BacktestRun.status == RUN_DONE)).all()}
    bad = [i for i in req.run_ids if i not in done]
    if bad:
        raise HTTPException(400, f"Backtest run(s) {bad} are not finished runs.")
    try:
        return _detail(train_model(session, req.run_ids, settings.ml_min_expected_r))
    except MlExtrasMissing as exc:
        raise HTTPException(409, str(exc)) from exc
    except MlTrainingError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/models/{model_id}/activate", response_model=ForecastModelDetail)
def activate(model_id: int, session: Session = Depends(get_session)) -> ForecastModelDetail:
    m = set_active(session, model_id)
    if m is None:
        raise HTTPException(404, "Model not found")
    return _detail(m)


@router.post("/models/deactivate", status_code=204)
def deactivate(session: Session = Depends(get_session)) -> None:
    set_active(session, None)


@router.delete("/models/{model_id}", status_code=204)
def delete_model(model_id: int, session: Session = Depends(get_session)) -> None:
    m = session.get(MlModel, model_id)
    if m is None:
        raise HTTPException(404, "Model not found")
    session.delete(m)
    session.commit()


@router.post("/sleeve", response_model=SleeveSchema, status_code=201)
def create_ml_sleeve(session: Session = Depends(get_session)) -> SleeveSchema:
    """The paper account the ML style trades in (one). Created on request, empty."""
    existing = session.exec(select(Sleeve).where(Sleeve.style == ML_SLEEVE_STYLE)).first()
    if existing is not None:
        raise HTTPException(409, "An ML sleeve already exists.")
    try:
        sleeve = create_sleeve(
            session,
            ML_SLEEVE_NAME,
            ML_SLEEVE_STYLE,
            100_000.0,
            "Opens only through an ordinary evaluation; the model can only stop a trade the rules approved.",
        )
    except SleeveError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return SleeveSchema(**sleeve.model_dump(), is_core=False)


@router.get("/predictions", response_model=list[ForecastPrediction])
def predictions(limit: int = 50, session: Session = Depends(get_session)) -> list[ForecastPrediction]:
    limit = max(1, min(limit, 200))
    rows = session.exec(select(MlPrediction).order_by(MlPrediction.id.desc()).limit(limit)).all()
    symbols = {
        p.id: p.symbol
        for p in session.exec(select(TradePlanRecord).where(TradePlanRecord.id.in_([r.plan_id for r in rows]))).all()  # type: ignore[attr-defined]
    } if rows else {}
    return [
        ForecastPrediction(
            id=r.id,
            plan_id=r.plan_id,
            symbol=symbols.get(r.plan_id),
            created_at=r.created_at,
            model_id=r.model_id,
            available=r.available,
            expected_r=r.expected_r,
            min_expected_r=r.min_expected_r,
            stopped_trade=r.stopped_trade,
            top_features=json.loads(r.top_features_json or "[]"),
            base_value=r.base_value,
            note=r.note,
        )
        for r in rows
    ]
