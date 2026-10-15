"""Hold mode (PORTOFOLIO_BUILDER.md step 1): no TP, no time exit, a close-based disaster stop."""
from __future__ import annotations

from datetime import timedelta

import pytest

from tests.test_time_exit import (  # reuse the fixtures and bar builders
    ENTRY_DAY, STARTING_CASH, BarsProvider, bar, clock_at_et, make_position, quiet_bars, session, trading_days_after,
)
from app.portfolio.engine import PaperTradingEngine

__all__ = ["session"]


def hold_position(session, **kw):
    p = make_position(session, **kw)
    p.mode, p.tp1, p.tp2 = "hold", 0.0, 0.0
    session.add(p)
    session.commit()
    return p


def sweep(session, bars, last_day, **kw):
    engine = PaperTradingEngine(
        session, BarsProvider(bars), STARTING_CASH, 
        clock=clock_at_et(last_day + timedelta(days=1), 12), **kw,
    )
    return engine.mark_to_market(snapshot=False)


def with_day(n, **fields):
    days = trading_days_after(ENTRY_DAY, n)
    rows = quiet_bars(n)
    rows[-1] = bar(days[-1], **fields)
    return rows, days[-1]


def test_no_time_exit_and_no_tp(session):
    p = hold_position(session)
    rows, last = with_day(6, o=100.0, h=130.0, low=99.0, c=125.0)  # far past the old 110 target, past max_holding_days
    assert sweep(session, rows, last) == []
    assert p.status == "open"


def test_close_beyond_stop_closes_at_that_close(session):
    p = hold_position(session)
    rows, last = with_day(3, o=96.0, h=97.0, low=90.0, c=92.0)
    sweep(session, rows, last, slippage_bps=10.0)
    assert p.close_reason == "disaster_stop"
    assert p.close_price == pytest.approx(92.0 * 0.999)


def test_wick_through_stop_that_closes_above_does_nothing(session):
    p = hold_position(session)
    rows, last = with_day(3, o=100.0, h=101.0, low=90.0, c=99.0)
    assert sweep(session, rows, last) == []
    assert p.status == "open"


def test_stop_hit_earlier_in_the_walk_still_closes(session):
    p = hold_position(session)
    days = trading_days_after(ENTRY_DAY, 4)
    rows = quiet_bars(4)
    rows[2] = bar(days[1], o=94.0, h=95.0, low=93.0, c=94.0)  # closes under 95, later bars recover
    sweep(session, rows, days[-1])
    assert p.close_reason == "disaster_stop"
    assert p.close_price == pytest.approx(94.0)


def test_entry_bar_is_excluded(session):
    p = hold_position(session)
    rows = quiet_bars(2)
    rows[0] = bar(ENTRY_DAY, c=80.0)  # the spent entry bar
    assert sweep(session, rows, trading_days_after(ENTRY_DAY, 2)[-1]) == []
    assert p.status == "open"


def test_stop_zero_is_alert_only(session):
    p = hold_position(session)
    p.stop_loss = 0.0
    session.add(p)
    session.commit()
    rows, last = with_day(3, o=50.0, h=51.0, low=40.0, c=45.0)
    assert sweep(session, rows, last) == []
    assert p.status == "open"


def test_hold_alerts_fire_once_per_condition():
    from app.portfolio.models import PaperPosition
    from app.services.price_alert_service import hold_alerts_due

    p = PaperPosition(symbol="X", direction="long", entry_price=100.0, stop_loss=75.0, tp1=0.0, tp2=0.0, shares=1, mode="hold")
    assert hold_alerts_due(p, 90.0, 80.0, 15.0) == []
    assert hold_alerts_due(p, 84.0, 90.0, 15.0) == ["hold_drawdown", "hold_ma200"]
    assert hold_alerts_due(p, 84.0, None, 0.0) == []  # drawdown alert off, no 200-day yet
    p.alerted_drawdown_at = p.alerted_ma200_at = object()
    assert hold_alerts_due(p, 50.0, 90.0, 15.0) == []


# ------------------------------------------------------------------ touch stop mode


def test_touch_mode_closes_a_wick_that_recovered_but_close_mode_does_not(session):
    p = hold_position(session)
    rows, last = with_day(3, o=100.0, h=101.0, low=90.0, c=99.0)  # touched 95, closed back above
    assert sweep(session, rows, last) == []  # close mode (the engine default)
    assert p.status == "open"
    sweep(session, rows, last, hold_stop_mode="touch")
    assert p.close_reason == "disaster_stop"
    assert p.close_price == pytest.approx(95.0)


def test_touch_mode_gap_down_fills_at_the_open_not_the_stop(session):
    p = hold_position(session)
    rows, last = with_day(3, o=90.0, h=96.0, low=88.0, c=99.0)
    sweep(session, rows, last, hold_stop_mode="touch")
    assert p.close_price == pytest.approx(90.0)


def test_touch_mode_never_counts_the_entry_bar_or_an_unfinished_bar(session):
    p = hold_position(session)
    rows = quiet_bars(2)
    rows[0] = bar(ENTRY_DAY, o=100.0, h=101.0, low=80.0, c=100.0)  # pre-entry wick under the stop
    days = trading_days_after(ENTRY_DAY, 2)
    assert sweep(session, rows, days[-1], hold_stop_mode="touch") == []
    rows[-1] = bar(days[-1], o=100.0, h=101.0, low=90.0, c=99.0)
    engine = PaperTradingEngine(session, BarsProvider(rows), STARTING_CASH, hold_stop_mode="touch",
                                clock=clock_at_et(days[-1], 12))  # that bar is still forming
    assert engine.mark_to_market(snapshot=False) == []
    assert p.status == "open"


def test_live_price_at_or_under_the_stop_closes_at_that_price(session):
    p = hold_position(session)
    days = trading_days_after(ENTRY_DAY, 1)

    class Live(BarsProvider):
        def __init__(self, price):
            super().__init__([])
            self.price = price

        def get_quote(self, symbol):
            q = super().get_quote(symbol)
            q.price = self.price
            return q

    def check(price, mode="touch", hour=12):
        engine = PaperTradingEngine(session, Live(price), STARTING_CASH, hold_stop_mode=mode, slippage_bps=10.0,
                                    clock=clock_at_et(days[0], hour))
        return engine.check_touch_stops(snapshot=False)

    assert check(94.0, mode="close") == []  # close mode never uses the live check
    assert check(94.0, hour=8) == []  # market closed
    assert check(96.0) == []  # above the stop
    closed = check(90.0)  # gapped under the stop: fills at the live price, not 95
    assert closed and p.close_reason == "disaster_stop"
    assert p.close_price == pytest.approx(90.0 * 0.999)
