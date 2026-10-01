"""The Sharpe-ratio statistics, checked against numbers worked out by hand from the formulas."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.backtest import deflated_sharpe as ds


def test_expected_max_sharpe_matches_hand_computed_values():
    # N=10, V=1: (1-g)*Z(0.9) + g*Z(1-1/(10e)) = 0.42278*1.28155 + 0.57722*1.7898... = 1.5746
    assert ds.expected_max_sharpe(1.0, 10) == pytest.approx(1.5746, abs=2e-4)
    # N=100: 0.42278*2.32635 + 0.57722*2.68029 = 2.5306
    assert ds.expected_max_sharpe(1.0, 100) == pytest.approx(2.5306, abs=2e-4)
    # It scales with the spread of the trials: sqrt(V).
    assert ds.expected_max_sharpe(0.25, 100) == pytest.approx(2.5306 / 2, abs=2e-4)


def test_no_luck_bonus_for_a_single_trial_or_no_spread():
    assert ds.expected_max_sharpe(1.0, 1) == 0.0
    assert ds.expected_max_sharpe(0.0, 50) == 0.0
    assert ds.expected_max_sharpe(-1.0, 50) == 0.0


def test_expected_max_sharpe_grows_with_the_number_of_trials():
    values = [ds.expected_max_sharpe(1.0, n) for n in (2, 5, 20, 100, 1000)]
    assert values == sorted(values) and len(set(values)) == len(values)


def test_psr_normal_returns_matches_hand_computation():
    # SR 0.1 per period over 101 observations, normal returns: z = 0.1 * 10 / sqrt(1 + 0.1^2/2) = 0.99751
    assert ds.probabilistic_sharpe_ratio(0.1, 0.0, 101) == pytest.approx(0.8407, abs=1e-4)
    assert ds.sharpe_standard_error(0.1, 101) == pytest.approx(math.sqrt(1.005 / 100))


def test_psr_is_half_when_the_sharpe_equals_the_benchmark():
    assert ds.probabilistic_sharpe_ratio(0.2, 0.2, 500, -0.3, 4.0) == pytest.approx(0.5)


def test_fat_tails_and_negative_skew_lower_the_psr():
    normal = ds.probabilistic_sharpe_ratio(0.1, 0.0, 101)
    # variance term 1 - (-0.5)(0.1) + (5-1)/4 * 0.01 = 1.06 -> z = 0.97129
    fat = ds.probabilistic_sharpe_ratio(0.1, 0.0, 101, skewness=-0.5, kurtosis=5.0)
    assert fat == pytest.approx(0.8343, abs=1e-4)
    assert fat < normal


def test_dsr_is_the_psr_against_the_expected_luck():
    # luck = 0.1 * 1.5746 = 0.15746; z = (0.1 - 0.15746) * 10 / sqrt(1.005) = -0.5732
    assert ds.deflated_sharpe_ratio(0.1, 101, 10, 0.01) == pytest.approx(0.2833, abs=2e-4)
    luck = ds.expected_max_sharpe(0.01, 10)
    assert ds.deflated_sharpe_ratio(0.1, 101, 10, 0.01) == ds.probabilistic_sharpe_ratio(0.1, luck, 101)


def test_dsr_with_one_trial_equals_the_psr_against_zero():
    assert ds.deflated_sharpe_ratio(0.1, 101, 1, 0.5) == pytest.approx(ds.probabilistic_sharpe_ratio(0.1, 0.0, 101))


def test_more_trials_never_raise_the_dsr():
    values = [ds.deflated_sharpe_ratio(0.12, 252, n, 0.02) for n in (1, 2, 10, 100)]
    assert values == sorted(values, reverse=True)


def test_min_track_record_length():
    # 1 + (1 + 0.1^2/2) * (1.6449 / 0.1)^2 = 272.9 observations
    assert ds.min_track_record_length(0.1, 0.0) == pytest.approx(272.9, abs=0.1)
    assert ds.min_track_record_length(0.1, 0.1) is None
    assert ds.min_track_record_length(0.05, 0.1) is None


def test_sharpe_moments_of_known_series():
    returns = np.array([0.01, -0.01] * 50 + [0.02] * 10)
    moments = ds.sharpe_moments(returns)
    assert moments.n == 110
    assert moments.sharpe == pytest.approx(returns.mean() / returns.std(ddof=1))
    # a symmetric two-point series has no skew and kurtosis 1
    sym = ds.sharpe_moments(np.array([0.01, -0.01] * 20))
    assert sym.skewness == pytest.approx(0.0, abs=1e-9)
    assert sym.kurtosis == pytest.approx(1.0)


def test_sharpe_moments_refuses_short_or_flat_series():
    assert ds.sharpe_moments([0.01] * 5) is None
    assert ds.sharpe_moments([0.01] * 50) is None
    assert ds.sharpe_moments([]) is None


def test_trial_sharpe_variance_ignores_undefined_and_needs_two():
    assert ds.trial_sharpe_variance([None, 0.1]) is None
    assert ds.trial_sharpe_variance([0.1, 0.3, None]) == pytest.approx(0.02)


def test_annualise():
    assert ds.annualise(0.1) == pytest.approx(0.1 * math.sqrt(252))
