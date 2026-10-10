from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.backtest.data_provider import PriceBook
from app.catalyst.scoring import KIND_EARNINGS, read_catalysts
from app.catalyst.study import MAX_HOLD_BARS, plan_levels, simulate_long
from app.knowledge import FactKind, record_fact


def _series(rows, start="2026-01-01"):
    days = pd.bdate_range(start, periods=len(rows))
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
    frame.insert(0, "date", days)
    return PriceBook.from_frames({"T": frame}).series["T"]


def _flat(n, price=100.0):
    return [(price, price + 1, price - 1, price, 1000.0)] * n


def test_levels_use_the_closer_of_atr_stop_and_support():
    s = _series(_flat(40))
    stop, target = plan_levels(s, 35, 100.0)
    # ATR is 2, the ATR stop 97, support 99: the closer is 99, but never closer than one ATR (98).
    assert stop == pytest.approx(98.0)
    assert target == pytest.approx(104.0)


def test_stop_wins_when_one_bar_holds_both_levels():
    rows = _flat(40) + [(100, 110, 90, 100, 1000.0)] + _flat(15)
    s = _series(rows)
    trade = simulate_long(s, 40, 98.0, 104.0, 100.0)
    assert trade.reason == "stop" and trade.r < 0


def test_gap_past_the_target_fills_at_the_open_better():
    rows = _flat(40) + [(100, 101, 99, 100, 1000.0), (108, 110, 107, 109, 1000.0)] + _flat(15)
    s = _series(rows)
    trade = simulate_long(s, 40, 98.0, 104.0, 100.0)
    assert trade.reason == "target" and trade.exit_price == pytest.approx(108 * 0.9995)


def test_gap_below_the_stop_fills_at_the_open_worse():
    rows = _flat(40) + [(100, 101, 99, 100, 1000.0), (94, 95, 93, 94, 1000.0)] + _flat(15)
    s = _series(rows)
    trade = simulate_long(s, 40, 98.0, 104.0, 100.0)
    assert trade.reason == "stop" and trade.exit_price == pytest.approx(94 * 0.9995)


def test_time_exit_after_the_hold_limit_at_the_close():
    s = _series(_flat(60))
    trade = simulate_long(s, 40, 98.0, 104.0, 100.0)
    assert trade.reason == "time" and trade.exit_day == s.days[40 + MAX_HOLD_BARS].astype(object)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _earnings(session, report_day, surprise, known_at):
    record_fact(
        session, kind=FactKind.EARNINGS_REPORT, source="test", dedupe_key=f"T-{report_day}", symbol="T",
        payload={"report_date": report_day.isoformat(), "eps_estimate": 1.0, "eps_actual": 1.1, "surprise_pct": surprise},
        known_at=known_at,
    )


def test_an_earnings_beat_counts_only_once_it_is_public(session):
    _earnings(session, date(2026, 3, 10), 12.0, datetime(2026, 3, 10, 21, 0))
    before = read_catalysts(session, "T", datetime(2026, 3, 10, 13, 45))
    after = read_catalysts(session, "T", datetime(2026, 3, 11, 13, 45))
    assert before.net == 0 and not before.covered[KIND_EARNINGS]
    assert after.by_kind == {KIND_EARNINGS: 1}


def test_a_miss_is_negative_and_stale_news_expires(session):
    _earnings(session, date(2026, 3, 10), -9.0, datetime(2026, 3, 10, 21, 0))
    assert read_catalysts(session, "T", datetime(2026, 3, 11, 13, 45)).net == -1
    assert read_catalysts(session, "T", datetime(2026, 3, 20, 13, 45)).net == 0  # beyond the window


def test_no_stored_data_is_not_scored_as_quiet(session):
    reading = read_catalysts(session, "T", datetime(2026, 3, 11, 13, 45))
    assert reading.net == 0 and not any(reading.covered.values())
