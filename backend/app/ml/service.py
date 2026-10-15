"""Forecast Lab logic: train on stored backtests (only when asked) and keep the results."""

from __future__ import annotations

import json
import logging

from sqlmodel import Session, select

from app.ml.dataset import load_rows, walk_forward_split
from app.ml.features import FEATURE_COMPONENTS, FEATURE_NAMES
from app.ml.models import MlModel
from app.ml.runtime import MlExtrasMissing, require_extras

logger = logging.getLogger(__name__)

ML_SLEEVE_STYLE = "ml"
ML_SLEEVE_NAME = "ML Forecast"
MIN_ROWS = 100


class MlTrainingError(ValueError):
    """Training cannot proceed (too few trades, bad selection). Message is user-facing."""


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


__all__ = [
    "FEATURE_COMPONENTS",
    "MIN_ROWS",
    "MlExtrasMissing",
    "MlTrainingError",
    "ML_SLEEVE_NAME",
    "ML_SLEEVE_STYLE",
    "active_model",
    "set_active",
    "train_model",
]
