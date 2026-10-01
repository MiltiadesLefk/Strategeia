"""How much of a backtest's Sharpe ratio could be luck: the probabilistic and the
deflated Sharpe ratio.

Written from the maths in Bailey and Lopez de Prado, "The Deflated Sharpe Ratio:
Correcting for Selection Bias, Backtest Overfitting and Non-Normality" (2014); the
idea of putting it next to a backtest comes from the ML4T book's code
(stefan-jansen/machine-learning-for-trading, MIT). Only the standard library is used.

Everything here works on **per-period** (e.g. daily) Sharpe ratios, the ratio of the
mean return to its standard deviation with no annualising. `annualise` scales one up
to a yearly figure for display only; the probability formulas need the native one.

Three ideas:

* A Sharpe ratio measured on `n` returns is itself a noisy estimate. Its standard
  error grows with fat tails (kurtosis) and shrinks with a positive skew, so the
  *probabilistic* Sharpe ratio (PSR) is the probability that the true Sharpe is above
  a benchmark, given the estimate, the sample length and the shape of the returns.
* If the Sharpe you report is the best of `N` things you tried, it is biased upward
  even when none of them has any skill. The expected best Sharpe of `N` skill-less
  trials is a known quantity (`expected_max_sharpe`), driven by `N` and by how much
  the trials' Sharpe ratios differ from one another.
* The *deflated* Sharpe ratio (DSR) is the PSR measured against that expected best
  luck, not against zero: "what is the chance this strategy is better than what
  trying N times would have given by luck alone".
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np

_NORMAL = NormalDist()
EULER_MASCHERONI = 0.5772156649015329
TRADING_DAYS_PER_YEAR = 252
# A variance term at or below this is treated as degenerate (it cannot be negative
# for real returns; a rounding error or an absurd skew input could push it there).
MIN_VARIANCE_TERM = 1e-12
# The usual cut for "probably real": the DSR must reach this.
DEFAULT_CONFIDENCE = 0.95
# Fewer observations than this give no meaningful skew/kurtosis, so no Sharpe statistics.
MIN_OBSERVATIONS = 10
# A return series whose standard deviation is below this is flat, not a strategy.
MIN_STDEV = 1e-12


@dataclass(frozen=True)
class SharpeMoments:
    """A return series reduced to what the Sharpe statistics need (all per-period)."""

    sharpe: float
    skewness: float
    kurtosis: float  # plain kurtosis (a normal distribution is 3), not excess
    n: int


def sharpe_moments(returns: Sequence[float] | np.ndarray) -> SharpeMoments | None:
    """Per-period Sharpe (risk-free rate 0), skewness and kurtosis of `returns`, or
    None when the series is too short or flat to have them."""
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]
    n = len(values)
    if n < MIN_OBSERVATIONS:
        return None
    std = float(np.std(values, ddof=1))
    if std < MIN_STDEV:
        return None
    centred = values - values.mean()
    m2 = float(np.mean(centred**2))
    skewness = float(np.mean(centred**3) / m2**1.5)
    kurtosis = float(np.mean(centred**4) / m2**2)
    return SharpeMoments(sharpe=float(values.mean() / std), skewness=skewness, kurtosis=kurtosis, n=n)


def annualise(per_period_sharpe: float, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    return per_period_sharpe * math.sqrt(periods_per_year)


def _variance_term(sharpe: float, skewness: float, kurtosis: float) -> float:
    """1 - skew*SR + (kurtosis-1)/4 * SR^2: the variance of the Sharpe estimate, per
    observation (it is 1 + SR^2/2 for normal returns)."""
    return 1.0 - skewness * sharpe + (kurtosis - 1.0) / 4.0 * sharpe**2


def sharpe_standard_error(sharpe: float, n: int, skewness: float = 0.0, kurtosis: float = 3.0) -> float:
    """Standard error of a per-period Sharpe estimated from `n` returns."""
    variance = max(_variance_term(sharpe, skewness, kurtosis), MIN_VARIANCE_TERM)
    return math.sqrt(variance / max(n - 1, 1))


def probabilistic_sharpe_ratio(
    sharpe: float, benchmark_sharpe: float, n: int, skewness: float = 0.0, kurtosis: float = 3.0
) -> float:
    """Probability that the true Sharpe exceeds `benchmark_sharpe` (both per-period),
    given a Sharpe estimated from `n` returns with this skewness and kurtosis."""
    z = (sharpe - benchmark_sharpe) / sharpe_standard_error(sharpe, n, skewness, kurtosis)
    return _NORMAL.cdf(z)


def expected_max_sharpe(trial_sharpe_variance: float, n_trials: int) -> float:
    """The best per-period Sharpe `n_trials` skill-less trials are expected to show,
    given the variance of the trials' Sharpe ratios:

        sqrt(V) * ((1 - g) * Z(1 - 1/N) + g * Z(1 - 1/(N e)))

    with g the Euler-Mascheroni constant and Z the inverse normal CDF. One trial (or
    no spread between trials) expects no luck bonus: 0."""
    if n_trials <= 1 or trial_sharpe_variance <= 0:
        return 0.0
    z_first = _NORMAL.inv_cdf(1.0 - 1.0 / n_trials)
    z_second = _NORMAL.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(trial_sharpe_variance) * ((1.0 - EULER_MASCHERONI) * z_first + EULER_MASCHERONI * z_second)


def trial_sharpe_variance(trial_sharpes: Sequence[float | None]) -> float | None:
    """Sample variance of the trials' per-period Sharpe ratios (undefined ones are
    left out), or None with fewer than two."""
    values = [s for s in trial_sharpes if s is not None and math.isfinite(s)]
    if len(values) < 2:
        return None
    return float(np.var(values, ddof=1))


def deflated_sharpe_ratio(
    sharpe: float,
    n: int,
    n_trials: int,
    trial_variance: float,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """The PSR measured against the best Sharpe luck alone is expected to produce
    over `n_trials` tries. With `n_trials` of 1 it equals the PSR against zero."""
    return probabilistic_sharpe_ratio(sharpe, expected_max_sharpe(trial_variance, n_trials), n, skewness, kurtosis)


def min_track_record_length(
    sharpe: float, benchmark_sharpe: float, skewness: float = 0.0, kurtosis: float = 3.0,
    confidence: float = DEFAULT_CONFIDENCE,
) -> float | None:
    """How many per-period returns are needed before this Sharpe beats the benchmark
    at `confidence`; None when the Sharpe is not above the benchmark at all (no
    amount of data would show it)."""
    if sharpe <= benchmark_sharpe:
        return None
    z = _NORMAL.inv_cdf(confidence)
    variance = max(_variance_term(sharpe, skewness, kurtosis), MIN_VARIANCE_TERM)
    return 1.0 + variance * (z / (sharpe - benchmark_sharpe)) ** 2
