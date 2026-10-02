"""Liquidity-aware slippage: extra bps on market fills that grow with order size
relative to average daily volume. Opt-in; off must leave every fill unchanged."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session

from app.config import AppSettings
from app.portfolio.liquidity_slippage import MAX_LIQUIDITY_SLIPPAGE_BPS, impact_bps
from app.strategy.snapshot import build_snapshot, fingerprint
from app.timeutil import utcnow_naive
from tests.test_exit_realism import BarsProvider, bars_from, build_engine, make_plan, session  # noqa: F401


def test_impact_is_square_root_of_participation():
    assert impact_bps(10, 1_000, 100.0) == pytest.approx(10.0)  # 1% of ADV
    assert impact_bps(40, 1_000, 100.0) == pytest.approx(20.0)  # 4x the size, 2x the cost


def test_impact_is_capped_and_zero_without_data():
    assert impact_bps(10_000, 1_000, 100.0) == MAX_LIQUIDITY_SLIPPAGE_BPS
    assert impact_bps(10, None, 100.0) == 0.0
    assert impact_bps(10, 0, 100.0) == 0.0
    assert impact_bps(0, 1_000, 100.0) == 0.0
    assert impact_bps(10, 1_000, 0.0) == 0.0


def test_off_changes_nothing(session: Session):  # noqa: F811
    provider = BarsProvider([], price=100.0, avg_volume=1_000.0)
    position = build_engine(session, provider, slippage_bps=5.0).open_position(make_plan(session))
    assert position.entry_price == pytest.approx(100.05)
    assert position.entry_slippage_bps is None


def test_entry_pays_extra_for_size_and_records_it(session: Session):  # noqa: F811
    provider = BarsProvider([], price=100.0, avg_volume=1_000.0)  # 10 shares = 1% of ADV
    engine = build_engine(session, provider, slippage_bps=5.0, liquidity_slippage_coefficient=100.0)
    position = engine.open_position(make_plan(session))
    assert position.entry_slippage_bps == pytest.approx(15.0)
    assert position.entry_price == pytest.approx(100.05 * 1.001)


def test_short_entry_is_filled_lower(session: Session):  # noqa: F811
    provider = BarsProvider([], price=100.0, avg_volume=1_000.0)
    engine = build_engine(session, provider, slippage_bps=5.0, liquidity_slippage_coefficient=100.0)
    plan = make_plan(session, direction="short", stop=105.0, tp1=90.0, tp2=80.0)
    position = engine.open_position(plan)
    assert position.entry_price == pytest.approx(99.95 * 0.999)


def test_stop_exit_pays_extra_but_target_does_not(session: Session):  # noqa: F811
    opened = utcnow_naive() - timedelta(days=2)
    provider = BarsProvider(bars_from(opened, [(100, 101, 99, 100), (96, 97, 90, 92)]), price=100.0, avg_volume=1_000.0)
    engine = build_engine(session, provider, slippage_bps=5.0, liquidity_slippage_coefficient=100.0)
    position = engine.open_position(make_plan(session))
    position.opened_at = opened
    session.add(position)
    session.commit()
    closed = engine.mark_to_market()
    assert closed[0].close_reason == "stop_hit"
    assert closed[0].close_price == pytest.approx(95.0 * (1 - 0.0015))
    assert closed[0].exit_slippage_bps == pytest.approx(15.0)

    provider2 = BarsProvider(bars_from(opened, [(100, 101, 99, 100), (108, 112, 108, 111)]), price=100.0, avg_volume=1_000.0)
    engine2 = build_engine(session, provider2, slippage_bps=5.0, liquidity_slippage_coefficient=100.0)
    position2 = engine2.open_position(make_plan(session, symbol="MSFT"))
    position2.opened_at = opened
    session.add(position2)
    session.commit()
    closed2 = engine2.mark_to_market()
    assert closed2[0].close_reason == "tp1_hit"
    assert closed2[0].close_price == pytest.approx(110.0)
    assert closed2[0].exit_slippage_bps is None


def test_extra_slippage_that_breaks_the_cash_check_resizes(session: Session):  # noqa: F811
    provider = BarsProvider([], price=100.0, avg_volume=1_000.0)
    engine = build_engine(session, provider, liquidity_slippage_coefficient=100.0)
    position = engine.open_position(make_plan(session, suggested_shares=1_000))  # all the cash, 100% of ADV: +100 bps
    assert position.entry_price == pytest.approx(101.0)
    assert position.shares == 990  # 1,000 shares at the slipped price would not fit the cash


def test_fingerprint_moves_only_while_enabled():
    base = fingerprint(build_snapshot(AppSettings()))
    assert fingerprint(build_snapshot(AppSettings(liquidity_slippage_coefficient=250.0))) == base
    on = fingerprint(build_snapshot(AppSettings(liquidity_slippage_enabled=True)))
    assert on != base
    assert fingerprint(build_snapshot(AppSettings(liquidity_slippage_enabled=True, liquidity_slippage_coefficient=250.0))) != on
