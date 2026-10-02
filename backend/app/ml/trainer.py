"""Fits the LightGBM model and explains predictions with SHAP.

lightgbm and shap are imported INSIDE the functions, so importing this module (and
the app) works without them; callers check `runtime.require_extras()` first.

Model choice uses the validation window only (early stopping and the pick between
a few small settings). The test window is looked at once, after the pick, and is
never used to choose anything.

Idea and TreeExplainer usage follow the gradient-boosting and SHAP chapters of
stefan-jansen/machine-learning-for-trading (MIT); the code here is our own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from app.ml import stats
from app.ml.dataset import Split
from app.ml.features import FEATURE_NAMES
from app.ml.runtime import require_extras

SEED = 7
MAX_ROUNDS = 300
EARLY_STOP = 20
# Small trees on purpose: a few hundred trades cannot support more.
GRID = ({"num_leaves": 3, "min_data_in_leaf": 5}, {"num_leaves": 7, "min_data_in_leaf": 10})
TOP_FEATURES = 5


@dataclass
class Trained:
    model_text: str
    best_iteration: int
    params: dict[str, Any]
    metrics: dict[str, Any]
    importance: list[dict[str, Any]]


def _params(variant: dict[str, int]) -> dict[str, Any]:
    return {
        "objective": "regression",
        "metric": "l2",
        "learning_rate": 0.05,
        "feature_fraction": 1.0,
        "seed": SEED,
        "deterministic": True,
        "force_row_wise": True,
        "num_threads": 1,
        "verbose": -1,
        **variant,
    }


def _matrix(rows) -> tuple[np.ndarray, np.ndarray]:
    return np.array([r.features for r in rows], dtype=float), np.array([r.r for r in rows], dtype=float)


def train(split: Split, min_expected_r: float = 0.0) -> Trained:
    require_extras()
    import lightgbm as lgb

    x_tr, y_tr = _matrix(split.train)
    x_va, y_va = _matrix(split.validation)
    x_te, y_te = _matrix(split.test)
    names = list(FEATURE_NAMES)
    d_train = lgb.Dataset(x_tr, label=y_tr, feature_name=names, free_raw_data=False)
    d_val = lgb.Dataset(x_va, label=y_va, reference=d_train, feature_name=names)

    best = None
    for variant in GRID:
        params = _params(variant)
        booster = lgb.train(
            params, d_train, num_boost_round=MAX_ROUNDS, valid_sets=[d_val],
            callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False)],
        )
        score = booster.best_score["valid_0"]["l2"]
        if best is None or score < best[0]:
            best = (score, booster, params)
    assert best is not None
    val_l2, booster, params = best
    iteration = int(booster.best_iteration or booster.current_iteration())

    pred_te = booster.predict(x_te, num_iteration=iteration)
    ic = stats.ic_with_interval(pred_te, y_te)
    kept = y_te[pred_te >= min_expected_r]
    stopped = y_te[pred_te < min_expected_r]
    metrics = {
        "validation_l2": float(val_l2),
        "test_l2": float(np.mean((pred_te - y_te) ** 2)),
        "test_baseline_l2": float(np.mean((np.mean(y_tr) - y_te) ** 2)),  # always guessing the train mean
        "ic": ic,
        "all_test_trades": stats.mean_with_interval(y_te),
        "kept_by_model": stats.mean_with_interval(kept),
        "stopped_by_model": stats.mean_with_interval(stopped),
        "min_expected_r": min_expected_r,
        "verdict": stats.verdict(len(y_te), ic),
    }

    explainer = _explainer(booster, iteration)
    contrib = np.abs(np.asarray(explainer.shap_values(x_te)))
    importance = [
        {"feature": n, "mean_abs_shap": float(v)} for n, v in zip(names, contrib.mean(axis=0))
    ]
    importance.sort(key=lambda d: d["mean_abs_shap"], reverse=True)
    return Trained(booster.model_to_string(num_iteration=iteration), iteration, params, metrics, importance)


def _explainer(booster, iteration: int):
    import shap

    return shap.TreeExplainer(booster)


_LOADED: dict[int, Any] = {}


def load_booster(model_text: str):
    require_extras()
    import lightgbm as lgb

    key = hash(model_text)
    if key not in _LOADED:
        if len(_LOADED) > 4:
            _LOADED.clear()
        _LOADED[key] = lgb.Booster(model_str=model_text)
    return _LOADED[key]


def predict_with_shap(model_text: str, features: list[float]) -> tuple[float, float, list[dict[str, Any]]]:
    """(expected R, SHAP base value, top contributions sorted by size) for one row."""
    booster = load_booster(model_text)
    x = np.array([features], dtype=float)
    expected = float(booster.predict(x)[0])
    explainer = _explainer(booster, 0)
    values = np.asarray(explainer.shap_values(x))[0]
    base = float(np.ravel(explainer.expected_value)[0])
    order = np.argsort(-np.abs(values))[:TOP_FEATURES]
    top = [{"feature": FEATURE_NAMES[i], "value": float(features[i]), "shap": float(values[i])} for i in order]
    return expected, base, top
