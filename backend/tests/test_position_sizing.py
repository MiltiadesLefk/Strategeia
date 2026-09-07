from __future__ import annotations

from app.risk.position_sizing import calculate_position_size, derive_targets


def test_calculate_position_size_basic():
    result = calculate_position_size(account_size=10_000, risk_pct=1, entry=150, stop=145)
    assert result.risk_per_share == 5
    assert result.account_risk_dollars == 100
    assert result.shares == 20
    assert result.position_value == 3000
    assert result.capped_by_cash is False


def test_calculate_position_size_capped_by_available_cash():
    result = calculate_position_size(account_size=10_000, risk_pct=1, entry=150, stop=145, available_cash=1_000)
    assert result.shares == 6  # floor(1000 / 150)
    assert result.capped_by_cash is True


def test_calculate_position_size_zero_risk_per_share():
    result = calculate_position_size(account_size=10_000, risk_pct=1, entry=150, stop=150)
    assert result.shares == 0


def test_derive_targets_long_uses_resistance_levels():
    targets = derive_targets(entry=100, stop=95, direction="long", support_levels=[90], resistance_levels=[110, 130])
    assert targets.tp1 == 110
    assert targets.tp2 == 130
    assert targets.rr1 == 2.0
    assert targets.rr2 == 6.0


def test_derive_targets_long_falls_back_to_r_multiples_without_levels():
    targets = derive_targets(entry=100, stop=95, direction="long", support_levels=[], resistance_levels=[])
    assert targets.tp1 == 107.5
    assert targets.tp2 == 115.0
    assert targets.rr1 == 1.5
    assert targets.rr2 == 3.0


def test_derive_targets_short_uses_support_levels():
    targets = derive_targets(entry=100, stop=105, direction="short", support_levels=[85, 70], resistance_levels=[])
    assert targets.tp1 == 85
    assert targets.tp2 == 70
    assert targets.rr1 == 3.0
    assert targets.rr2 == 6.0
