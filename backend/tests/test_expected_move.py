"""Options-implied expected move.

The core idea being tested: this is a MAGNITUDE read (how big a move the
options market is pricing), never a direction call — so it must penalize a
long and a short on the identical setup identically, unlike every
direction-aware scorer in this codebase.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.analysis.expected_move import (
    ELEVATED_RATIO_THRESHOLD,
    EXPECTED_MOVE_SCORE_CAP,
    MIN_DAYS_FOR_RELIABLE_READ,
    compute_expected_move_pct,
    days_to_expiration,
    score_expected_move,
)
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


def test_elevated_expected_move_penalizes_long_and_short_identically():
    """The whole point: this is not a directional opinion."""
    options = _options(iv=0.80, days_out=30)  # large IV -> large expected move
    long_score, long_reasons = score_expected_move("long", options, atr_pct=0.3)
    short_score, short_reasons = score_expected_move("short", options, atr_pct=0.3)
    assert long_score == short_score == -EXPECTED_MOVE_SCORE_CAP
    assert long_reasons == short_reasons


def test_ordinary_expected_move_contributes_nothing():
    """IV roughly matching the stock's own recent range is unremarkable —
    only a meaningfully elevated ratio should fire."""
    options = _options(iv=0.20, days_out=30)
    # ATR-scaled realized move over 30 days at atr_pct=1.5%/day: 1.5*sqrt(30)≈8.2%
    # expected move at IV=0.20 over 30d: 0.20*sqrt(30/365)*100≈5.7% — well under
    score, reasons = score_expected_move("long", options, atr_pct=1.5)
    assert score == 0
    assert reasons == []


def test_missing_options_data_contributes_nothing():
    assert score_expected_move("long", None, atr_pct=1.0) == (0, [])


def test_missing_atr_contributes_nothing():
    options = _options(iv=0.50, days_out=30)
    assert score_expected_move("long", options, atr_pct=None) == (0, [])
    assert score_expected_move("long", options, atr_pct=0.0) == (0, [])


def test_missing_iv_contributes_nothing():
    options = _options(iv=None, days_out=30)
    assert score_expected_move("long", options, atr_pct=1.0) == (0, [])


def test_near_dated_expiration_is_excluded_as_unreliable():
    """Same reasoning YFinanceProvider already applies when CHOOSING an
    expiration — this module re-checks it because it can't see which
    expiration a caller picked."""
    options = _options(iv=0.80, days_out=MIN_DAYS_FOR_RELIABLE_READ - 1)
    assert score_expected_move("long", options, atr_pct=0.3) == (0, [])


def test_ratio_exactly_at_threshold_is_elevated():
    """Sanity check the boundary is inclusive-of-elevated, i.e. `< threshold`
    is the "not elevated" branch, not `<=`."""
    days = 30
    atr_pct = 1.0
    realized = atr_pct * (days ** 0.5)
    target_expected = realized * ELEVATED_RATIO_THRESHOLD
    # solve iv from compute_expected_move_pct(iv, days) == target_expected
    iv = target_expected / 100 / (days / 365.0) ** 0.5
    options = _options(iv=iv, days_out=days)
    score, _ = score_expected_move("long", options, atr_pct=atr_pct)
    assert score == -EXPECTED_MOVE_SCORE_CAP
