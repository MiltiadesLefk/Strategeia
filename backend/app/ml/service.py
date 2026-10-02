"""Forecast Lab logic: train on stored backtests (only when asked), keep results,
and give the one opinion the trade-plan service may consult.

The opinion can only STOP a trade (expected R below the limit). It is asked only
for a sleeve whose style is "ml", only when `ml_style_enabled` is on, and only for a
trade the rules already approved. Any failure means "no opinion", never an error."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, select

from app.config import AppSettings
from app.ml.dataset import load_rows, walk_forward_split
from app.ml.features import FEATURE_COMPONENTS, FEATURE_NAMES, feature_vector
from app.ml.models import MlModel, MlPrediction
from app.ml.runtime import MlExtrasMissing, extras_status, require_extras
from app.portfolio.models import Sleeve

logger = logging.getLogger(__name__)

ML_SLEEVE_STYLE = "ml"
ML_SLEEVE_NAME = "ML Forecast"
MIN_ROWS = 100


class MlTrainingError(ValueError):
    """Training cannot proceed (too few trades, bad selection). Message is user-facing."""


@dataclass
class MlOpinion:
    available: bool
    expected_r: float | None = None
    min_expected_r: float | None = None
    stops_trade: bool = False
    top_features: list[dict[str, Any]] = field(default_factory=list)
    base_value: float | None = None
    model_id: int | None = None
    note: str | None = None


def active_model(session: Session) -> MlModel | None:
    return session.exec(select(MlModel).where(MlModel.active == True).order_by(MlModel.id.desc())).first()  # noqa: E712


def train_model(session: Session, run_ids: list[int], min_expected_r: float = 0.0) -> MlModel:
    """Fits and stores a model from the closed trades of `run_ids`. Never activates it."""
    require_extras()
    from app.ml import trainer

    run_ids = sorted(set(run_ids))
    rows = load_rows(session, run_ids)
    if len(rows) < MIN_ROWS:
        raise MlTrainingError(
            f"Only {len(rows)} closed backtest trades with stored scores; at least {MIN_ROWS} are needed. "
            "Run longer or wider backtests first."
        )
    split = walk_forward_split(rows)
    if split is None:
        raise MlTrainingError("The trades cover too few distinct days to cut into train, validation and test windows.")
    trained = trainer.train(split, min_expected_r)
    constant = [
        FEATURE_NAMES[i] for i in range(len(FEATURE_NAMES)) if len({r.features[i] for r in rows}) == 1
    ]
    notes = []
    if constant:
        notes.append(
            "Constant in training (the backtest never produced them, so the model cannot use them): "
            + ", ".join(constant)
            + ". Backtests are price-only; live plans also carry fundamentals, news and options points."
        )
    if len(split.test) < 20:
        notes.append(f"Only {len(split.test)} out-of-sample trades: treat every metric as noise.")
    record = MlModel(
        run_ids_json=json.dumps(run_ids),
        feature_names_json=json.dumps(list(FEATURE_NAMES)),
        model_text=trained.model_text,
        best_iteration=trained.best_iteration,
        params_json=json.dumps(trained.params),
        split_json=json.dumps(
            {
                "n_rows": len(rows),
                "n_train": len(split.train),
                "n_validation": len(split.validation),
                "n_test": len(split.test),
                "validation_start": split.validation_start.isoformat(),
                "test_start": split.test_start.isoformat(),
                "purged_rows": split.purged,
            }
        ),
        metrics_json=json.dumps(trained.metrics),
        importance_json=json.dumps(trained.importance),
        notes_json=json.dumps(notes),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def set_active(session: Session, model_id: int | None) -> MlModel | None:
    """Makes one model the active one (None switches the model off)."""
    target = None
    if model_id is not None:
        target = session.get(MlModel, model_id)
        if target is None:
            return None
    for m in session.exec(select(MlModel).where(MlModel.active == True)).all():  # noqa: E712
        m.active = False
        session.add(m)
    if target is not None:
        target.active = True
        session.add(target)
    session.commit()
    if target is not None:
        session.refresh(target)
    return target


def ml_opinion_for_plan(
    session: Session,
    settings: AppSettings,
    sleeve: Sleeve | None,
    *,
    scores: dict[str, Any],
    confidence_points: int | None,
    direction: str | None,
) -> MlOpinion | None:
    """The model's opinion for one approved trade, or None when the ML style is not
    in play (setting off, or not an "ml" sleeve). An opinion with available=False
    never stops anything: the rules then decide alone."""
    if not settings.ml_style_enabled or sleeve is None or sleeve.style != ML_SLEEVE_STYLE:
        return None
    limit = settings.ml_min_expected_r
    try:
        model = active_model(session)
        if model is None:
            return MlOpinion(False, min_expected_r=limit, note="No active model: rules decide alone.")
        status = extras_status()
        if not status.available:
            return MlOpinion(False, min_expected_r=limit, model_id=model.id, note=status.message)
        from app.ml import trainer

        features = feature_vector(scores, confidence_points, direction)
        expected, base, top = trainer.predict_with_shap(model.model_text, features)
        return MlOpinion(True, expected, limit, expected < limit, top, base, model.id)
    except Exception as exc:  # noqa: BLE001 - an opinion must never break a plan
        logger.warning("ML opinion failed, rules decide alone: %s", exc)
        return MlOpinion(False, min_expected_r=limit, note=f"Model could not be read ({type(exc).__name__}).")


def record_ml_opinion(session: Session, plan_id: int | None, opinion: MlOpinion | None) -> None:
    """Stores the opinion beside the plan it was about (best effort)."""
    if opinion is None or plan_id is None:
        return
    try:
        session.add(
            MlPrediction(
                plan_id=plan_id,
                model_id=opinion.model_id,
                available=opinion.available,
                expected_r=opinion.expected_r,
                min_expected_r=opinion.min_expected_r,
                stopped_trade=opinion.stops_trade,
                top_features_json=json.dumps(opinion.top_features),
                base_value=opinion.base_value,
                note=opinion.note,
            )
        )
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("Could not store the ML opinion for plan %s: %s", plan_id, exc)


def ml_stop_reason(opinion: MlOpinion, direction: str | None) -> str:
    top = ", ".join(f"{t['feature']} ({t['shap']:+.2f})" for t in opinion.top_features[:3])
    return (
        f"ML model expects {opinion.expected_r:+.2f}R, below the {opinion.min_expected_r:+.2f}R limit — "
        f"no trade taken against a rule-based {direction}." + (f" Main drivers: {top}." if top else "")
    )


__all__ = [
    "FEATURE_COMPONENTS",
    "MIN_ROWS",
    "MlExtrasMissing",
    "MlOpinion",
    "MlTrainingError",
    "ML_SLEEVE_NAME",
    "ML_SLEEVE_STYLE",
    "active_model",
    "ml_opinion_for_plan",
    "ml_stop_reason",
    "record_ml_opinion",
    "set_active",
    "train_model",
]
