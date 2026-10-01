"""Small-sample statistics for judging whether a score relates to results.

Pure functions, no database and no network: numpy and the standard library
only (scipy is not a dependency, and nothing here needs it).

Everything is built for the situation this app is actually in: tens of closed
trades, not thousands. So each function returns an interval or a p-value next
to the point estimate, and every random procedure uses a fixed seed so the same
trades always give the same report (a GET that changed on every refresh would
look like a finding).

The idea of judging a signal by its rank correlation with later returns (the
"information coefficient") and of putting an honest interval on it comes from
the ML-for-trading literature (Stefan Jansen, "Machine Learning for Trading",
chapter 7, MIT). The code below is written from scratch for this app.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import permutations
from typing import Sequence

import numpy as np

# 95% two-sided intervals everywhere.
CONFIDENCE_LEVEL = 0.95
Z_95 = 1.959963984540054

# Fixed seeds: the report is a pure function of the stored trades.
BOOTSTRAP_SEED = 20260930
PERMUTATION_SEED = 20260931
BOOTSTRAP_RESAMPLES = 2000
PERMUTATION_RESAMPLES = 5000
# Up to this many pairs the permutation test enumerates every ordering
# (8! = 40,320) instead of sampling, so the p-value is exact where it is cheap.
EXACT_PERMUTATION_MAX_N = 8
# Permutations are generated in chunks so a few thousand trades never build a
# multi-hundred-megabyte matrix.
PERMUTATION_CHUNK_CELLS = 2_000_000

# A rank correlation needs at least this many pairs before the Fieller
# standard error (which divides by n - 3) is defined.
MIN_PAIRS_FOR_INTERVAL = 4
# Keeps atanh finite when a sample is perfectly correlated.
_ATANH_CLIP = 1 - 1e-9


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float] | None:
    """Wilson score interval for a proportion, as fractions in 0..1.

    Used for win rates instead of the textbook p +/- z*sqrt(p(1-p)/n): that
    version collapses to a zero-width interval at 0 or n wins and is far too
    narrow for small n, which is exactly when win rates get over-read.
    None when there are no trials."""
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def bootstrap_mean_interval(
    values: Sequence[float],
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float] | None:
    """Percentile bootstrap interval for the mean. None below two values (one
    number has no spread to resample). It makes no normality assumption, which
    matters for R: a trade's result is capped on the downside near -1R but can
    run well past +2R, so it is skewed, not bell-shaped."""
    arr = np.asarray(values, dtype=float)
    n = len(arr)
    if n < 2:
        return None
    rng = np.random.default_rng(seed)
    means = np.empty(resamples)
    # Chunked so memory stays bounded for large n.
    chunk = max(1, PERMUTATION_CHUNK_CELLS // n)
    done = 0
    while done < resamples:
        size = min(chunk, resamples - done)
        idx = rng.integers(0, n, size=(size, n))
        means[done : done + size] = arr[idx].mean(axis=1)
        done += size
    alpha = (1 - CONFIDENCE_LEVEL) / 2
    low, high = np.quantile(means, [alpha, 1 - alpha])
    return float(low), float(high)


def average_ranks(values: Sequence[float]) -> np.ndarray:
    """Ranks starting at 1, tied values sharing the average of the ranks they
    span (the standard treatment for Spearman, and the one that matters here:
    confidence points are integers with many ties)."""
    arr = np.asarray(values, dtype=float)
    order = np.argsort(arr, kind="mergesort")
    sorted_arr = arr[order]
    ranks_sorted = np.empty(len(arr))
    i = 0
    while i < len(arr):
        j = i
        while j + 1 < len(arr) and sorted_arr[j + 1] == sorted_arr[i]:
            j += 1
        ranks_sorted[i : j + 1] = (i + j) / 2 + 1  # average of ranks i+1 .. j+1
        i = j + 1
    ranks = np.empty(len(arr))
    ranks[order] = ranks_sorted
    return ranks


def _centred_unit(ranks: np.ndarray) -> np.ndarray | None:
    centred = ranks - ranks.mean()
    norm = float(np.sqrt((centred**2).sum()))
    return None if norm == 0 else centred / norm


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Spearman rank correlation: Pearson correlation of the average ranks.
    None when it is undefined (fewer than two pairs, or one side constant)."""
    if len(x) != len(y):
        raise ValueError("x and y must be the same length")
    if len(x) < 2:
        return None
    ux = _centred_unit(average_ranks(x))
    uy = _centred_unit(average_ranks(y))
    if ux is None or uy is None:
        return None
    return float(max(-1.0, min(1.0, ux @ uy)))


def rank_correlation_p_value(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Two-sided permutation p-value for the Spearman correlation: how often
    would shuffling the results against the scores give a correlation at least
    this far from zero? Exact (every ordering) up to EXACT_PERMUTATION_MAX_N
    pairs, otherwise PERMUTATION_RESAMPLES shuffles with the usual +1 so a
    p-value is never reported as exactly zero.

    A permutation test is used instead of the textbook t-approximation because
    it stays valid for few pairs and many ties, both of which are normal here."""
    n = len(x)
    if n < 2:
        return None
    ux = _centred_unit(average_ranks(x))
    uy = _centred_unit(average_ranks(y))
    if ux is None or uy is None:
        return None
    observed = abs(float(ux @ uy))
    tolerance = 1e-12  # float noise must not decide whether a tie counts as "as extreme"

    if n <= EXACT_PERMUTATION_MAX_N:
        total = 0
        extreme = 0
        for perm in permutations(range(n)):
            total += 1
            if abs(float(ux @ uy[list(perm)])) >= observed - tolerance:
                extreme += 1
        return extreme / total

    rng = np.random.default_rng(PERMUTATION_SEED)
    chunk = max(1, PERMUTATION_CHUNK_CELLS // n)
    extreme = 0
    done = 0
    while done < PERMUTATION_RESAMPLES:
        size = min(chunk, PERMUTATION_RESAMPLES - done)
        idx = np.argsort(rng.random((size, n)), axis=1)
        correlations = uy[idx] @ ux
        extreme += int((np.abs(correlations) >= observed - tolerance).sum())
        done += size
    return (extreme + 1) / (PERMUTATION_RESAMPLES + 1)


def spearman_interval(rho: float, n: int) -> tuple[float, float] | None:
    """95% interval for a Spearman correlation via the Fisher z-transform with
    the Fieller-Hartley-Pearson standard error sqrt((1 + rho^2 / 2) / (n - 3)),
    which is the usual correction for ranks (plain Pearson's 1/sqrt(n - 3) is
    too narrow for them). Needs at least MIN_PAIRS_FOR_INTERVAL pairs."""
    if n < MIN_PAIRS_FOR_INTERVAL:
        return None
    clipped = max(-_ATANH_CLIP, min(_ATANH_CLIP, rho))
    z = math.atanh(clipped)
    se = math.sqrt((1 + clipped * clipped / 2) / (n - 3))
    return math.tanh(z - Z_95 * se), math.tanh(z + Z_95 * se)


@dataclass(frozen=True)
class RankCorrelation:
    n: int
    rho: float | None
    p_value: float | None
    ci_low: float | None
    ci_high: float | None
    distinct_x: int
    distinct_y: int


def rank_correlation(x: Sequence[float], y: Sequence[float]) -> RankCorrelation:
    """Spearman correlation with its p-value and interval, and how many
    distinct values each side had (a score that never varies cannot relate to
    anything, and the caller wants to say that rather than show a blank)."""
    n = len(x)
    distinct_x = len(set(x))
    distinct_y = len(set(y))
    rho = spearman(x, y)
    if rho is None:
        return RankCorrelation(n, None, None, None, None, distinct_x, distinct_y)
    interval = spearman_interval(rho, n)
    return RankCorrelation(
        n=n,
        rho=rho,
        p_value=rank_correlation_p_value(x, y),
        ci_low=interval[0] if interval else None,
        ci_high=interval[1] if interval else None,
        distinct_x=distinct_x,
        distinct_y=distinct_y,
    )
