"""Measuring how far live results have moved from what the backtest showed.

Pure functions, no database. The measure is the population stability index (PSI)
of the per-trade result in R (a multiple of the amount risked), comparing the live
closed trades against a backtest's trades. PSI and its usual bands (below 0.10
stable, 0.10 to 0.25 watch, above 0.25 act) follow the drift-monitoring notebooks
of the "Machine Learning for Trading" repository; the code here is our own.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

# Bins are cut at the backtest's own quantiles so each holds the same share of it.
PSI_BINS = 5
# A zero count would make the log undefined; this floor is the usual smoothing.
_EPS = 1e-4
PSI_WARNING = 0.10
PSI_CRITICAL = 0.25


def psi(reference: Sequence[float], live: Sequence[float], bins: int = PSI_BINS) -> float | None:
    """PSI of `live` against `reference`, or None when either side is too small to
    cut into `bins` groups (fewer values than bins)."""
    if len(reference) < bins or len(live) < bins:
        return None
    ordered = sorted(reference)
    edges = [ordered[min(len(ordered) - 1, (len(ordered) * i) // bins)] for i in range(1, bins)]

    def shares(values: Sequence[float]) -> list[float]:
        counts = [0] * bins
        for v in values:
            counts[sum(1 for e in edges if v > e)] += 1
        return [max(c / len(values), _EPS) for c in counts]

    ref, cur = shares(reference), shares(live)
    return sum((c - r) * math.log(c / r) for r, c in zip(ref, cur))


def psi_band(value: float) -> str:
    if value >= PSI_CRITICAL:
        return "critical"
    if value >= PSI_WARNING:
        return "warning"
    return "stable"
