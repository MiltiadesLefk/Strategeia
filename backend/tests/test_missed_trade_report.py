"""The missed-trades report and its endpoints: the arithmetic on hand-picked R values,
the honesty rules (too early, unresolved, not simulated), and the read/refresh split."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_app_settings, get_session
from app.api.routers.missed_trades import get_missed_trade_bars_loader
from app.main import app
from app.portfolio.missed_trade_models import (
    OUTCOME_NO_DATA,
    OUTCOME_NOT_SIMULATABLE,
    OUTCOME_OPEN,
    OUTCOME_RESOLVED,
    MissedTradeOutcome,
)
from app.portfolio.missed_trade_report import (
    MAX_LISTED_TRADES,
    VERDICT_BAR_COSTS,
    VERDICT_BAR_JUSTIFIED,
    VERDICT_COST,
    VERDICT_NO_DATA,
    VERDICT_SAVED,
    VERDICT_TOO_EARLY,
    VERDICT_UNCLEAR,
    build_missed_trade_report,
)
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.portfolio.signal_stats import wilson_interval
from test_missed_trades import CLEAN, LONG_REASON, long_bars

VETO = "AI Trading Overlay would not take this trade at 80% conviction — no trade taken against a rule-based long."
NO_TREND = "No clear trend (EMA20/EMA50 not aligned) — not enough information to size a trade."
CREATED = datetime(2026, 9, 1, 21, 0)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def add_missed(
    session: Session,
    reason: str,
    r: float | None,
    *,
    status: str = OUTCOME_RESOLVED,
    symbol: str = "TEST",
    plan_status: str = "no_trade",
    note: str | None = None,
    created_at: datetime = CREATED,
    with_row: bool = True,
) -> TradePlanRecord:
    plan = TradePlanRecord(
        symbol=symbol, status=plan_status, reason=reason if plan_status == "no_trade" else None, confidence_score=20,
        auto_execute_note=note, created_at=created_at,
        **({} if plan_status == "no_trade" else dict(direction="long", entry=100.0, stop=95.0, tp1=107.0)),
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    if with_row:
        session.add(
            MissedTradeOutcome(
                plan_id=plan.id, symbol=symbol, category="x", status=status, direction="long", entry=100.0, stop=95.0,
                tp1=107.0, r_multiple=r, trade_source="reconstructed",
                exit_reason="tp1_hit" if status == OUTCOME_RESOLVED else None,
                exit_date=date(2026, 9, 3) if status == OUTCOME_RESOLVED else None,
            )
        )
        session.commit()
    return plan


def add_taken(session: Session, r: float) -> None:
    session.add(
        PaperPosition(
            symbol="T", direction="long", entry_price=100.0, stop_loss=95.0, tp1=107.0, tp2=110.0, shares=10,
            status="closed", realized_r=r, realized_pnl=r * 50, close_reason="manual",
        )
    )
    session.commit()


def category(report, key):
    return next(c for c in report.categories if c.key == key)


# --------------------------------------------------------------- the arithmetic


def test_category_numbers_are_the_hand_computed_ones(session):
    for r in (-1.0, -1.0, 2.0, -0.5):
        add_missed(session, VETO, r)
    veto = category(build_missed_trade_report(session), "ai_veto")

    assert (veto.n, veto.resolved, veto.wins) == (4, 4, 1)
    assert veto.win_rate == pytest.approx(25.0)
    # Wilson 95% for 1 of 4, worked by hand: centre 0.37247, half-width 0.32691
    assert veto.win_rate_low == pytest.approx(4.56, abs=0.05)
    assert veto.win_rate_high == pytest.approx(69.94, abs=0.05)
    low, high = wilson_interval(1, 4)
    assert (veto.win_rate_low, veto.win_rate_high) == (pytest.approx(low * 100), pytest.approx(high * 100))
    assert veto.avg_r == pytest.approx(-0.125)
    assert veto.total_r == pytest.approx(-0.5)
    assert veto.avg_r_low <= veto.avg_r <= veto.avg_r_high
    assert veto.small_sample


def test_a_win_is_a_result_above_zero(session):
    for r in (0.0, 0.01, -0.01):
        add_missed(session, LONG_REASON, r)
    assert category(build_missed_trade_report(session), "low_confidence").wins == 1


def test_open_awaiting_and_not_simulated_are_counted_apart_from_resolved(session):
    add_missed(session, VETO, 1.0)
    add_missed(session, VETO, 0.4, status=OUTCOME_OPEN)
    add_missed(session, VETO, 0.9, status=OUTCOME_OPEN)
    add_missed(session, VETO, None, with_row=False)  # no row yet: needs a refresh
    add_missed(session, VETO, None, status=OUTCOME_NO_DATA)  # prices could not be loaded
    add_missed(session, VETO, None, status=OUTCOME_NOT_SIMULATABLE)
    add_missed(session, NO_TREND, None, with_row=False)  # neutral: counted, never simulated
    report = build_missed_trade_report(session)

    veto = category(report, "ai_veto")
    assert (veto.n, veto.resolved, veto.open, veto.awaiting, veto.not_simulated) == (6, 1, 2, 2, 1)
    assert veto.avg_r == pytest.approx(1.0) and veto.total_r == pytest.approx(1.0)  # the open marks are not in it
    assert veto.open_avg_r == pytest.approx(0.65)
    neutral = category(report, "neutral_trend")
    assert (neutral.n, neutral.resolved, neutral.not_simulated, neutral.awaiting) == (1, 0, 1, 0)
    assert neutral.avg_r is None and neutral.win_rate is None
    assert report.awaiting_refresh == 2 and report.plans_considered == 7


def test_not_executed_plans_are_split_by_the_engines_reason(session):
    add_missed(session, "", 1.0, plan_status="pending", note="Auto-execute skipped: Already at the 5-position cap (x) — y")
    add_missed(session, "", -1.0, plan_status="pending", note="Auto-execute skipped: Already at the 5-position cap (x) — y")
    add_missed(session, "", 0.5, plan_status="pending", note="Auto-execute held: AI Trading Overlay called this bearish")
    add_missed(session, "", None, plan_status="pending", note="Auto-execute skipped: AAPL already has an open position (id=3)", with_row=False)
    report = build_missed_trade_report(session)

    refused = category(report, "not_executed")
    assert refused.n == 3 and refused.resolved == 2 and refused.not_simulated == 1
    assert {(d.detail, d.n) for d in refused.details} == {("position_cap", 2), ("duplicate_position", 1)}
    assert category(report, "held_by_ai").resolved == 1


def test_executed_and_discarded_plans_are_left_out_even_with_a_stored_outcome(session):
    plan = add_missed(session, VETO, 1.0)
    plan.status = "executed"
    session.add(plan)
    session.commit()
    assert build_missed_trade_report(session).plans_considered == 0


# ------------------------------------------------------------------- the questions


def question(report, key):
    return next(q for q in report.questions if q.key == key)


def test_the_veto_question_says_too_early_below_the_minimum(session):
    for _ in range(5):
        add_missed(session, VETO, -1.0)
    answer = question(build_missed_trade_report(session), "ai_veto")
    assert answer.verdict == VERDICT_TOO_EARLY
    assert "5 vetoed trades" in answer.answer and "-1.00R" in answer.answer


def test_the_veto_question_with_nothing_resolved(session):
    add_missed(session, VETO, None, with_row=False)
    answer = question(build_missed_trade_report(session), "ai_veto")
    assert answer.verdict == VERDICT_NO_DATA and "Refresh" in answer.answer


@pytest.mark.parametrize(
    ("r_values", "verdict"),
    [([-1.0] * 20, VERDICT_SAVED), ([1.0] * 20, VERDICT_COST), ([2.0, -1.0] * 10, VERDICT_UNCLEAR)],
)
def test_the_veto_verdict_follows_the_interval_not_the_point_estimate(session, r_values, verdict):
    for r in r_values:
        add_missed(session, VETO, r)
    answer = question(build_missed_trade_report(session), "ai_veto")
    assert answer.verdict == verdict


def test_the_veto_saved_message_names_the_r_kept_out_of_the_account(session):
    for _ in range(20):
        add_missed(session, VETO, -1.0)
    assert "+20.00R out of the account" in question(build_missed_trade_report(session), "ai_veto").answer


def test_the_confidence_bar_question_compares_with_the_closed_trades(session):
    for _ in range(20):
        add_missed(session, LONG_REASON, -1.0)
        add_taken(session, 1.0)
    report = build_missed_trade_report(session)
    assert report.taken.n == 20 and report.taken.avg_r == pytest.approx(1.0) and report.taken.total_r == pytest.approx(20.0)
    assert question(report, "confidence_bar").verdict == VERDICT_BAR_JUSTIFIED


def test_the_confidence_bar_may_cost_money(session):
    for _ in range(20):
        add_missed(session, LONG_REASON, 1.0)
        add_taken(session, -1.0)
    assert question(build_missed_trade_report(session), "confidence_bar").verdict == VERDICT_BAR_COSTS


def test_the_confidence_bar_question_needs_enough_of_both(session):
    for _ in range(20):
        add_missed(session, LONG_REASON, -1.0)
    add_taken(session, 1.0)
    answer = question(build_missed_trade_report(session), "confidence_bar")
    assert answer.verdict == VERDICT_TOO_EARLY and "Too early" in answer.answer


def test_the_confidence_bar_question_with_no_closed_trades(session):
    add_missed(session, LONG_REASON, -1.0)
    assert question(build_missed_trade_report(session), "confidence_bar").verdict == VERDICT_NO_DATA


def test_the_report_carries_its_caveats_and_the_sample_threshold(session):
    report = build_missed_trade_report(session)
    assert report.min_trades_for_reading == 20
    text = " ".join(report.caveats)
    assert "hypothetical" in text and "daily bars only" in text and "survivorship" in text


def test_the_list_is_newest_first_and_capped(session):
    add_missed(session, VETO, 1.0, symbol="OLD", created_at=CREATED - timedelta(days=3))
    add_missed(session, VETO, 1.0, symbol="NEW", created_at=CREATED)
    report = build_missed_trade_report(session)
    assert [t.symbol for t in report.trades] == ["NEW", "OLD"]
    assert report.trades[0].state == "resolved" and report.trades[0].exit_reason == "tp1_hit"
    assert MAX_LISTED_TRADES >= 100


def test_the_report_never_writes(session):
    add_missed(session, VETO, None, with_row=False)
    before = session.exec(select(MissedTradeOutcome)).all()
    build_missed_trade_report(session)
    build_missed_trade_report(session)
    assert session.exec(select(MissedTradeOutcome)).all() == before == []


# ----------------------------------------------------------------------- endpoints


class CountingLoader:
    def __init__(self, bars: pd.DataFrame):
        self.bars, self.calls = bars, 0

    def __call__(self, symbol, start):
        self.calls += 1
        return self.bars


@pytest.fixture
def api(session):
    loader = CountingLoader(long_bars([(200.0, 208.5, 199.0, 208.0)]))
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_app_settings] = lambda: CLEAN
    app.dependency_overrides[get_missed_trade_bars_loader] = lambda: loader
    yield TestClient(app), loader
    for dep in (get_session, get_app_settings, get_missed_trade_bars_loader):
        app.dependency_overrides.pop(dep, None)


def _plan_at_decision(session) -> TradePlanRecord:
    from test_missed_trades import DECISION_AFTER_CLOSE

    plan = TradePlanRecord(symbol="TEST", status="no_trade", reason=LONG_REASON, confidence_score=25, created_at=DECISION_AFTER_CLOSE)
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def test_get_reads_the_stored_outcomes_and_never_computes_or_writes(api, session):
    client, loader = api
    _plan_at_decision(session)

    body = client.get("/api/missed-trades").json()

    assert loader.calls == 0  # no price history was requested
    assert session.exec(select(MissedTradeOutcome)).all() == []
    assert body["awaiting_refresh"] == 1
    low = next(c for c in body["categories"] if c["key"] == "low_confidence")
    assert (low["n"], low["awaiting"], low["resolved"]) == (1, 1, 0)
    assert body["trades"][0]["state"] == "awaiting"
    assert body["last_computed_at"] is None


def test_refresh_computes_then_get_shows_the_result(api, session):
    client, loader = api
    _plan_at_decision(session)

    refreshed = client.post("/api/missed-trades/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["computed"] == 1 and refreshed.json()["resolved"] == 1
    assert loader.calls == 1

    body = client.get("/api/missed-trades").json()
    low = next(c for c in body["categories"] if c["key"] == "low_confidence")
    assert (low["resolved"], low["wins"]) == (1, 1)
    assert low["avg_r"] == pytest.approx(1.5)
    assert body["trades"][0]["r_multiple"] == pytest.approx(1.5)
    assert body["last_computed_at"].endswith("Z")
    assert loader.calls == 1  # the GET did not load prices


def test_refresh_has_a_cooldown(api, session):
    client, _ = api
    assert client.post("/api/missed-trades/refresh").status_code == 200
    second = client.post("/api/missed-trades/refresh")
    assert second.status_code == 429
    assert "wait" in second.json()["detail"]

