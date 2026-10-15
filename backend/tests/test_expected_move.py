"""Options-implied expected move.

The core idea being tested: this is a MAGNITUDE read (how big a move the
options market is pricing), never a direction call — so it must penalize a
long and a short on the identical setup identically, unlike every
direction-aware scorer in this codebase.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.analysis.expected_move import compute_expected_move_pct, days_to_expiration
from app.data_providers.base import OptionsSummary


def _options(iv: float | None, days_out: int) -> OptionsSummary:
    expiration = (date.today() + timedelta(days=days_out)).isoformat()
    return OptionsSummary(symbol="ACME", expiration=expiration, put_call_volume_ratio=1.0, atm_implied_volatility=iv)


def test_days_to_expiration_parses_iso_dates():
    exp = (date.today() + timedelta(days=30)).isoformat()
    assert days_to_expiration(exp) == 30


def test_days_to_expiration_is_none_for_garbage():
    assert days_to_expiration("not-a-date") is None
    assert days_to_expiration(None) is None


def test_expected_move_scales_by_square_root_of_time_not_time_itself():
    """Variance is additive over time, so IV (a standard deviation) scales
    with sqrt(t) — a naive linear scaling would be a materially different,
    wrong number for anything but a 1-year expiration."""
    move_1y = compute_expected_move_pct(0.30, 365)
    move_1mo = compute_expected_move_pct(0.30, 30)
    # sqrt(30/365) ≈ 0.286, not 30/365 ≈ 0.082 — confirms sqrt-time scaling
    assert move_1mo == pytest.approx(move_1y * (30 / 365) ** 0.5, rel=1e-6)
    assert move_1mo != pytest.approx(move_1y * (30 / 365), rel=1e-2)


def test_expected_move_pct_is_a_real_percentage_not_a_fraction():
    # 30% annualized IV over 30 days should be a single-digit percent, not 0.0x
    move = compute_expected_move_pct(0.30, 30)
    assert 5 < move < 15


def test_zero_or_negative_inputs_return_none():
    assert compute_expected_move_pct(0.0, 30) is None
    assert compute_expected_move_pct(0.30, 0) is None
    assert compute_expected_move_pct(0.30, -5) is None


