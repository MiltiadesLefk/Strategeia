"""The per-sleeve review report (NEW_PLAN section 8): read-only, honest below 100 trades, fixed seed."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_session
from app.main import app
from app.portfolio.models import PaperPosition, Sleeve, TradePlanRecord
from app.services import smart_money_sleeve_report_service as report
from app.services.smart_money_sleeve_report_service import MIN_CLOSED_TRADES, build_sleeve_report

START = date(2025, 1, 2)


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def make_loader(drift: dict[str, float]):
    """Daily closes for 430 calendar days; each symbol drifts by its own daily percentage."""

    def load(symbol: str, start: date, end: date) -> pd.DataFrame:
        if symbol not in drift:
            return pd.DataFrame(columns=["date", "open", "close"])
        rows = []
        d = START - timedelta(days=30)
        price = 100.0
        while d <= START + timedelta(days=400):
            price *= 1 + drift[symbol]
            if start <= d <= end:
                rows.append({"date": pd.Timestamp(d), "open": price, "close": price})
            d += timedelta(days=1)
        return pd.DataFrame(rows)

    return load


def add_sleeve(session, key) -> Sleeve:
    s = Sleeve(key=key, name=key, style="x", starting_cash=100000.0)
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def add_trades(session, sleeve_id, count, *, exit_mult=1.05, symbol="AAA", source=None, score=None):
    for i in range(count):
        plan = TradePlanRecord(symbol=symbol, status="executed", confidence_score=50, source=source, smart_money_score=score)
        session.add(plan)
        session.commit()
        session.refresh(plan)
        opened = datetime(2025, 1, 2) + timedelta(days=i % 30)
        session.add(
            PaperPosition(
                trade_plan_id=plan.id, symbol=symbol, direction="long", entry_price=100.0, stop_loss=95.0, tp1=110.0,
                tp2=120.0, shares=1, opened_at=opened, status="closed", closed_at=opened + timedelta(days=10),
                close_price=100.0 * exit_mult, realized_pnl=5.0, realized_r=1.0, sleeve_id=sleeve_id,
            )
        )
    session.commit()


def UNIVERSE() -> list[str]:  # noqa: N802
    return ["AAA", "BBB", "CCC"]


LOADER = make_loader({"SPY": 0.0005, "AAA": 0.0005, "BBB": 0.0005, "CCC": 0.0005})


def sleeve_row(resp, key):
    return next(s for s in resp.sleeves if s.key == key)


def test_below_100_closed_trades_the_verdict_is_not_enough_data_with_n(session):
    manual = add_sleeve(session, "smart-money-manual")
    add_trades(session, manual.id, MIN_CLOSED_TRADES - 1)
    row = sleeve_row(build_sleeve_report(session, loader=LOADER, universe=UNIVERSE), "smart-money-manual")
    assert row.verdict == "not_enough_data"
    assert row.closed_trades == MIN_CLOSED_TRADES - 1
    assert row.min_closed_trades == 100
    assert row.vs_spy is None and row.candidate_to_disable is False
    assert "99" in row.headline


def test_empty_sleeves_still_report_n_zero(session):
    resp = build_sleeve_report(session, loader=LOADER, universe=UNIVERSE)
    assert [s.key for s in resp.sleeves] == ["core", "smart-money-auto", "smart-money-manual"]
    assert all(s.verdict == "not_enough_data" and s.closed_trades == 0 for s in resp.sleeves)
    assert not any(s.exists for s in resp.sleeves if s.key != "core")


def test_a_sleeve_that_beats_neither_is_flagged_candidate_to_disable(session):
    auto = add_sleeve(session, "smart-money-auto")
    add_trades(session, auto.id, 100, exit_mult=0.9)
    row = sleeve_row(build_sleeve_report(session, loader=LOADER, universe=UNIVERSE), "smart-money-auto")
    assert row.verdict == "beats_neither"
    assert row.candidate_to_disable is True
    assert row.vs_spy.compared == 100 and row.vs_random.compared == 100
    assert row.vs_spy.low is not None and row.vs_spy.high is not None  # never a bare number


def test_a_sleeve_that_beats_both_is_not_flagged(session):
    auto = add_sleeve(session, "smart-money-auto")
    add_trades(session, auto.id, 100, exit_mult=1.5)
    row = sleeve_row(build_sleeve_report(session, loader=LOADER, universe=UNIVERSE), "smart-money-auto")
    assert row.verdict == "beats_both" and row.candidate_to_disable is False


def test_missing_price_history_degrades_to_not_enough_data_with_the_reason(session):
    auto = add_sleeve(session, "smart-money-auto")
    add_trades(session, auto.id, 100)
    row = sleeve_row(build_sleeve_report(session, loader=make_loader({}), universe=UNIVERSE), "smart-money-auto")
    assert row.verdict == "not_enough_data"
    assert "comparable" in (row.reason or "")
    assert row.vs_spy.available is False and row.candidate_to_disable is False


def test_the_random_baseline_uses_a_fixed_seed_and_is_reproducible(session):
    auto = add_sleeve(session, "smart-money-auto")
    add_trades(session, auto.id, 100)
    loader = make_loader({"SPY": 0.0005, "AAA": 0.0005, "BBB": 0.002, "CCC": -0.001})
    a = build_sleeve_report(session, loader=loader, universe=UNIVERSE)
    b = build_sleeve_report(session, loader=loader, universe=UNIVERSE)
    assert a.model_dump() == b.model_dump()
    assert a.random_seed == report.RANDOM_SEED
    assert report._draw_index(7, 3, 50) == report._draw_index(7, 3, 50)


def test_each_sleeve_counts_only_its_own_closed_trades(session):
    auto, manual = add_sleeve(session, "smart-money-auto"), add_sleeve(session, "smart-money-manual")
    add_trades(session, auto.id, 3)
    add_trades(session, manual.id, 5)
    add_trades(session, None, 2)  # core: sleeve_id NULL
    resp = build_sleeve_report(session, loader=LOADER, universe=UNIVERSE)
    assert {s.key: s.closed_trades for s in resp.sleeves} == {"core": 2, "smart-money-auto": 3, "smart-money-manual": 5}


def test_groups_split_by_source_and_score_band(session):
    auto = add_sleeve(session, "smart-money-auto")
    add_trades(session, auto.id, 2, source="smart_money", score=4.0)
    add_trades(session, auto.id, 1, source="smart_money", score=9.0)
    row = sleeve_row(build_sleeve_report(session, loader=LOADER, universe=UNIVERSE), "smart-money-auto")
    by_label = {g.label: g.n for g in row.groups}
    assert by_label == {"source: smart_money": 3, "score 3-5": 2, "score 8+": 1}
    assert all(g.small_sample for g in row.groups)


def test_the_endpoint_writes_nothing(engine, session, monkeypatch):
    auto = add_sleeve(session, "smart-money-auto")
    add_trades(session, auto.id, 4)

    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override
    models = [PaperPosition, Sleeve, TradePlanRecord]
    try:
        before = [len(session.exec(select(m)).all()) for m in models]
        resp = TestClient(app).get("/api/smart-money/sleeve-report")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["sleeves"][1]["closed_trades"] == 4
        assert body["sleeves"][1]["verdict"] == "not_enough_data"
        assert body["caveats"]
        session.expire_all()
        assert before == [len(session.exec(select(m)).all()) for m in models]
    finally:
        app.dependency_overrides.clear()
