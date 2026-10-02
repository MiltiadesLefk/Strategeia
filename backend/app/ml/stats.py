"""Small pure statistics for judging the model out of sample. numpy/pandas only.

Every figure is reported with its n and a bootstrap interval; below MIN_TEST_ROWS
the verdict is "not_enough_data" (same bar as the confidence report card)."""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_TEST_ROWS = 20
BOOTSTRAP_RESAMPLES = 500
SEED = 7


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return None
    ra, rb = pd.Series(a).rank().to_numpy(), pd.Series(b).rank().to_numpy()
    if np.std(ra) == 0 or np.std(rb) == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def bootstrap_interval(values: np.ndarray, stat, resamples: int = BOOTSTRAP_RESAMPLES) -> tuple[float, float] | None:
    """95% percentile interval of stat(values) from resampling rows."""
    n = len(values)
    if n < 3:
        return None
    rng = np.random.default_rng(SEED)
    draws = []
    for _ in range(resamples):
        sample = values[rng.integers(0, n, n)]
        v = stat(sample)
        if v is not None and np.isfinite(v):
            draws.append(v)
    if len(draws) < resamples // 2:
        return None
    return float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))


def mean_with_interval(r: np.ndarray) -> dict:
    n = len(r)
    if n == 0:
        return {"n": 0, "mean": None, "low": None, "high": None}
    ci = bootstrap_interval(r, lambda x: float(np.mean(x)))
    return {"n": n, "mean": float(np.mean(r)), "low": ci[0] if ci else None, "high": ci[1] if ci else None}


def ic_with_interval(pred: np.ndarray, actual: np.ndarray) -> dict:
    """Rank correlation of prediction and outcome, with n and a bootstrap interval
    (resampling (prediction, outcome) pairs together)."""
    n = len(pred)
    value = spearman(pred, actual)
    low = high = None
    if value is not None:
        pair = np.column_stack([pred, actual])
        ci = bootstrap_interval(pair, lambda p: spearman(p[:, 0], p[:, 1]))
        if ci:
            low, high = ci
    return {"n": n, "ic": value, "low": low, "high": high}


def verdict(n_test: int, ic: dict) -> str:
    if n_test < MIN_TEST_ROWS or ic["ic"] is None or ic["low"] is None:
        return "not_enough_data"
    if ic["low"] > 0:
        return "edge_out_of_sample"
    if ic["high"] is not None and ic["high"] < 0:
        return "inverted"
    return "no_clear_edge"
