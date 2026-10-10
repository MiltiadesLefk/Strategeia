from datetime import date, datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.catalyst.picker import catalyst_value, percentile_ranks, score_stocks, top_picks
from app.knowledge import FactKind, record_fact


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _earnings(session, symbol, surprise, report_day, known_at):
    record_fact(
        session, kind=FactKind.EARNINGS_REPORT, source="test", dedupe_key=f"{symbol}-{report_day}", symbol=symbol,
        payload={"report_date": report_day.isoformat(), "eps_estimate": 1.0, "eps_actual": 1.1, "surprise_pct": surprise},
        known_at=known_at,
    )


def test_a_fact_known_after_the_month_end_does_not_change_the_score(session):
    month_end = datetime(2026, 3, 31, 20, 30)
    _earnings(session, "AAA", 10.0, date(2026, 3, 20), datetime(2026, 3, 20, 21, 0))
    before = catalyst_value(session, "AAA", month_end)
    _earnings(session, "AAA", 40.0, date(2026, 4, 2), datetime(2026, 4, 2, 21, 0))  # public after the month-end
    assert catalyst_value(session, "AAA", month_end) == before == 1.0


def test_ranking_is_deterministic_and_ties_break_by_symbol():
    parts = {s: {"quality": 0.1, "momentum": 0.2, "catalyst": 0.0} for s in ("ZZZ", "AAA", "MMM")}
    first = [p.symbol for p in top_picks(score_stocks(parts), 3)]
    second = [p.symbol for p in top_picks(score_stocks(dict(reversed(list(parts.items())))), 3)]
    assert first == second == ["AAA", "MMM", "ZZZ"]
    assert percentile_ranks({"a": 1, "b": 1, "c": 3}) == {"a": 0.25, "b": 0.25, "c": 1.0}


def test_a_missing_part_is_flagged_and_not_scored_as_zero():
    parts = {
        "HAS": {"quality": 0.5, "momentum": 0.5, "catalyst": 0.0},
        "LOW": {"quality": 0.1, "momentum": 0.1, "catalyst": 0.0},
        "GAP": {"quality": None, "momentum": 0.9, "catalyst": 0.0},
    }
    got = {s.symbol: s for s in score_stocks(parts)}
    assert got["GAP"].missing == ("quality",) and "quality" not in got["GAP"].ranks
    assert got["GAP"].score == pytest.approx((1.0 + 0.5) / 2)  # momentum top, catalyst tied; not dragged down by 0
    assert got["HAS"].missing == ()


def test_no_stored_catalyst_data_is_a_missing_part(session):
    assert catalyst_value(session, "AAA", datetime(2026, 3, 31, 20, 30)) is None
