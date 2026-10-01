"""Replaying a settings change on stored decisions: numbers worked by hand on a
small fixture, the refusals, and the guarantee that nothing is written."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_app_settings, get_session
from app.backtest.replay import (
    FLIP_NOW_SKIPPED,
    FLIP_NOW_TAKEN,
    REASON_NO_TREND,
    RESULT_NONE,
    RESULT_RESOLVED,
    ReplayRow,
    UnreplayableKnobError,
    load_replay_rows,
    parse_overrides,
    points_from_score,
    replay_decisions,
)
from app.config import AppSettings
from app.main import app
from app.portfolio.missed_trade_models import OUTCOME_RESOLVED, MissedTradeOutcome
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.portfolio.signal_stats import wilson_interval

SETTINGS = AppSettings(min_confidence_for_trade=30, ai_overlay_objection_action="cancel", ai_overlay_scores_confidence=True)
WHEN = datetime(2026, 9, 1, 14, 45)


def row(plan_id, points, *, taken, r, direction="long", overlay=0, stance=None, verdict=None, conviction=None, state=RESULT_RESOLVED):
    return ReplayRow(
        plan_id=plan_id, symbol=f"S{plan_id}", created_at=WHEN.replace(day=plan_id), taken=taken, direction=direction,
        confidence_points=points, ai_overlay_score=overlay, ai_stance=stance, ai_verdict=verdict, ai_conviction=conviction,
        strategy_version=1, result_state=state, result_r=r,
    )


# Points to percentage: 4 -> 25%, 5 -> 31%, 6 -> 38%, 7 -> 44%. The bar is 30%.
FIXTURE = [
    row(1, 6, taken=True, r=2.0),
    row(2, 5, taken=True, r=-1.0),
    row(3, 7, taken=True, r=1.0, direction="short"),
    row(4, 4, taken=False, r=1.5),  # under the bar, would have won
    row(5, 3, taken=False, r=-1.0),  # under the bar, would have lost
    # Vetoed: the rules earned 6 points, the AI "pass" at 50% conviction cost 2 (stored points 4).
    row(6, 4, taken=False, r=2.0, overlay=-2, stance="neutral", verdict="pass", conviction=50),
]


def flipped(result):
    return {f.plan_id: f for f in result.flips}


def test_raising_the_bar_drops_the_two_marginal_taken_trades():
    result = replay_decisions(FIXTURE, SETTINGS, parse_overrides({"min_confidence_for_trade": 40}))
    # A (38%) and B (31%) fall under 40%; C (44%) stays.
    assert set(flipped(result)) == {1, 2}
    assert all(f.flip == FLIP_NOW_SKIPPED for f in result.flips)
    before, after = result.before, result.after
    assert (before.taken, before.resolved, before.wins, before.total_r) == (3, 3, 2, pytest.approx(2.0))
    assert before.avg_r == pytest.approx(2.0 / 3)
    assert before.win_rate == pytest.approx(200 / 3)
    low, high = wilson_interval(2, 3)
    assert (before.win_rate_low, before.win_rate_high) == (pytest.approx(low * 100), pytest.approx(high * 100))
    assert (after.taken, after.wins, after.total_r, after.avg_r, after.win_rate) == (
        1, 1, pytest.approx(1.0), pytest.approx(1.0), pytest.approx(100.0),
    )
    assert result.now_skipped == 2 and result.now_taken == 0 and result.fixed_count == 0


def test_lowering_the_bar_takes_the_25_percent_setup_with_its_hypothetical_result():
    result = replay_decisions(FIXTURE, SETTINGS, parse_overrides({"min_confidence_for_trade": 20}))
    # D is 25% -> taken (+1.5R). E is 19% -> still skipped. F is still vetoed by the objection.
    assert set(flipped(result)) == {4}
    assert flipped(result)[4].flip == FLIP_NOW_TAKEN and flipped(result)[4].result_r == 1.5
    assert (result.after.taken, result.after.wins, result.after.total_r) == (4, 3, pytest.approx(3.5))
    assert result.after.avg_r == pytest.approx(0.875)


def test_turning_the_objection_off_entirely_releases_the_vetoed_trade():
    # Objection does nothing and costs no points: the rules' 6 points (38%) clear the bar.
    result = replay_decisions(
        FIXTURE, SETTINGS, parse_overrides({"ai_overlay_objection_action": "none", "ai_overlay_scores_confidence": False})
    )
    assert set(flipped(result)) == {6}
    assert flipped(result)[6].points_after == 6
    assert result.after.total_r == pytest.approx(4.0)


def test_the_two_objection_settings_are_separate_questions():
    # No action but still costing points: 4 points = 25% is under the bar, so still skipped.
    only_action = replay_decisions(FIXTURE, SETTINGS, parse_overrides({"ai_overlay_objection_action": "none"}))
    assert only_action.flipped_count == 0
    # Cancel/hold are the same trigger: holding instead of cancelling moves nothing in a replay.
    hold = replay_decisions(FIXTURE, SETTINGS, parse_overrides({"ai_overlay_objection_action": "hold"}))
    assert hold.flipped_count == 0


def test_direction_filter_drops_shorts():
    result = replay_decisions(FIXTURE, SETTINGS, parse_overrides({"allowed_directions": "long"}))
    assert set(flipped(result)) == {3}
    assert result.after.taken == 2 and result.after.total_r == pytest.approx(1.0)


def test_a_flip_without_a_result_is_counted_but_adds_nothing_to_the_rates():
    rows = FIXTURE + [row(7, 4, taken=False, r=None, state=RESULT_NONE)]
    result = replay_decisions(rows, SETTINGS, parse_overrides({"min_confidence_for_trade": 20}))
    assert result.now_taken == 2 and result.flips_without_result == 1
    assert result.after.taken == 5 and result.after.resolved == 4 and result.after.no_result == 1
    assert result.after.total_r == pytest.approx(3.5)


def test_rows_no_setting_can_move_keep_what_happened():
    fixed = row(8, 7, taken=False, r=None, state=RESULT_NONE)
    fixed.fixed_reason = REASON_NO_TREND
    result = replay_decisions(FIXTURE + [fixed], SETTINGS, parse_overrides({"min_confidence_for_trade": 0}))
    assert result.fixed_count == 1 and 8 not in flipped(result)


def test_decisions_already_different_today_do_not_move_the_before_column():
    # Taken in fact although today's bar (60%) would skip it: raising the bar further changes nothing for it.
    rows = [row(1, 6, taken=True, r=2.0)]
    result = replay_decisions(rows, AppSettings(min_confidence_for_trade=60), parse_overrides({"min_confidence_for_trade": 70}))
    assert result.flipped_count == 0 and result.baseline_disagrees == 1
    assert result.before.total_r == result.after.total_r == pytest.approx(2.0)


def test_small_samples_are_labelled():
    assert replay_decisions(FIXTURE, SETTINGS, parse_overrides({"min_confidence_for_trade": 40})).after.small_sample


def test_points_round_trip_for_every_reachable_confidence():
    from app.services.trade_plan_service import _confidence_score

    assert all(points_from_score(_confidence_score(p)) == p for p in range(17))


# ----------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    "knob", ["default_risk_pct", "max_concurrent_positions", "slippage_bps", "max_holding_days", "paper_starting_cash"]
)
def test_knobs_that_change_size_or_exits_are_refused_with_a_reason(knob):
    with pytest.raises(UnreplayableKnobError) as err:
        parse_overrides({"min_confidence_for_trade": 40, knob: 1})
    assert knob in err.value.rejected and "Backtest Lab" in err.value.rejected[knob]


def test_unknown_and_other_settings_and_bad_values_are_refused():
    with pytest.raises(UnreplayableKnobError) as err:
        parse_overrides({"nope": 1, "telegram_chat_id": "x"})
    assert set(err.value.rejected) == {"nope", "telegram_chat_id"}
    with pytest.raises(ValueError):
        parse_overrides({})
    with pytest.raises(ValueError):
        parse_overrides({"min_confidence_for_trade": 101})
    with pytest.raises(ValueError):
        parse_overrides({"ai_overlay_objection_action": "explode"})


# ----------------------------------------------------------------- database


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def add_plan(session, symbol, points, *, status="no_trade", reason=None, **extra):
    plan = TradePlanRecord(
        symbol=symbol, status=status, reason=reason, confidence_score=round(points / 16 * 100), created_at=WHEN,
        strategy_version=2, **extra,
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def seed(session):
    taken = add_plan(session, "TAKEN", 6, status="executed", direction="long", entry=100.0, stop=95.0, tp1=107.0)
    session.add(
        PaperPosition(
            trade_plan_id=taken.id, symbol="TAKEN", direction="long", entry_price=100.0, stop_loss=95.0, tp1=107.0,
            tp2=110.0, shares=10, status="closed", realized_r=2.0, realized_pnl=100.0,
        )
    )
    low = add_plan(session, "LOW", 4, reason="Confidence too low (25%, needs 30%+) despite a bullish trend.")
    session.add(
        MissedTradeOutcome(
            plan_id=low.id, symbol="LOW", category="low_confidence", status=OUTCOME_RESOLVED, direction="long",
            r_multiple=1.5, exit_date=date(2026, 9, 5),
        )
    )
    veto = add_plan(
        session, "VETO", 4, ai_overlay_score=-2, ai_opinion_stance="neutral", ai_trade_verdict="pass", ai_opinion_score=50,
        reason="AI Trading Overlay would not take this trade at 50% conviction - no trade taken against a rule-based long.",
    )
    session.add(
        MissedTradeOutcome(
            plan_id=veto.id, symbol="VETO", category="ai_veto", status=OUTCOME_RESOLVED, direction="long",
            r_multiple=-1.0, exit_date=date(2026, 9, 5),
        )
    )
    add_plan(session, "FLAT", 0, reason="No clear trend (EMA20/EMA50 not aligned) - not enough information to size a trade.")
    session.commit()


def test_rows_are_built_from_plans_positions_and_missed_outcomes(session):
    seed(session)
    rows, truncated = load_replay_rows(session)
    by_symbol = {r.symbol: r for r in rows}
    assert not truncated and len(rows) == 4
    assert (by_symbol["TAKEN"].taken, by_symbol["TAKEN"].result_r, by_symbol["TAKEN"].confidence_points) == (True, 2.0, 6)
    assert (by_symbol["LOW"].taken, by_symbol["LOW"].result_r, by_symbol["LOW"].direction) == (False, 1.5, "long")
    assert by_symbol["VETO"].direction == "long" and by_symbol["VETO"].ai_overlay_score == -2
    assert by_symbol["FLAT"].fixed_reason == REASON_NO_TREND and by_symbol["TAKEN"].fixed_reason is None


def test_the_endpoint_replays_and_writes_nothing(session):
    seed(session)

    def counts():
        return [len(session.exec(select(m)).all()) for m in (TradePlanRecord, PaperPosition, MissedTradeOutcome)]

    before = counts()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_app_settings] = lambda: SETTINGS
    try:
        client = TestClient(app)
        ok = client.post("/api/replay", json={"overrides": {"min_confidence_for_trade": 20}})
        refused = client.post("/api/replay", json={"overrides": {"default_risk_pct": 2}})
        empty = client.post("/api/replay", json={"overrides": {}})
        extra = client.post("/api/replay", json={"overrides": {"min_confidence_for_trade": 20}, "save": True})
    finally:
        for dep in (get_session, get_app_settings):
            app.dependency_overrides.pop(dep, None)

    assert ok.status_code == 200
    body = ok.json()
    # LOW (25%) is now taken at +1.5R; TAKEN keeps +2.0R: 2 trades, 3.5R. VETO stays vetoed.
    assert (body["before"]["taken"], body["after"]["taken"], body["after"]["total_r"]) == (1, 2, 3.5)
    assert [f["symbol"] for f in body["flips"]] == ["LOW"] and body["flips"][0]["strategy_version"] == 2
    assert body["fixed_count"] == 1 and body["decisions"] == 4
    assert refused.status_code == 422 and "default_risk_pct" in refused.json()["detail"]["rejected"]
    assert empty.status_code == 422 and extra.status_code == 422
    assert counts() == before and not session.new and not session.dirty
