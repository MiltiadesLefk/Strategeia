"""The AI Committee as a gate on a trade the rules already approved (committee/gate.py)."""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.committee.gate import CommitteeGateBudget, agrees_with
from app.committee.models import CommitteeRun
from app.config import AppSettings
from app.data_providers.base import OptionsSummary
from app.portfolio.models import PaperPosition
from app.services import trade_plan_service
from app.services.automation_service import run_auto_scan
from tests.test_committee import FakeLLM
from tests.test_trade_plan_service import FakeUptrendDataProvider


class GateData(FakeUptrendDataProvider):
    """The plan-service fake, plus the extra reads the committee's data pack makes."""

    def get_earnings_estimate(self, symbol):
        return None

    def get_options_summary(self, symbol):
        return OptionsSummary(symbol, "2099-01-15", 0.62, 0.35)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def settings(**overrides) -> AppSettings:
    base = dict(
        telegram_bot_token="",
        telegram_chat_id="",
        ai_trading_overlay_enabled=False,
        auto_execute_trade_plans=True,
        committee_gate_enabled=True,
    )
    base.update(overrides)
    return AppSettings(**base)


def final(rating: str) -> str:
    return json.dumps({"rating": rating, "conviction": "high", "summary": "Because.", "key_risks": "Some."})


def plan(session, llm, *, gate=True, **overrides):
    return trade_plan_service.generate_trade_plan(
        "AAPL",
        100_000.0,
        1.0,
        GateData(),
        llm,
        session,
        settings=settings(**overrides),
        committee_gate=CommitteeGateBudget(5) if gate else None,
    )


# ------------------------------------------------------------------ the reading rule


@pytest.mark.parametrize(
    "rating,direction,expected",
    [
        ("Buy", "long", True),
        ("Overweight", "long", True),
        ("Hold", "long", False),
        ("Underweight", "long", False),
        ("Sell", "long", False),
        ("Sell", "short", True),
        ("Underweight", "short", True),
        ("Hold", "short", False),
        ("Buy", "short", False),
        (None, "long", None),
    ],
)
def test_rating_backs_only_its_own_direction(rating, direction, expected):
    assert agrees_with(rating, direction) is expected


def test_budget_runs_out():
    budget = CommitteeGateBudget(2)
    assert [budget.take(), budget.take(), budget.take()] == [True, True, False]


# ------------------------------------------------------------------ the gate in a plan


def test_backing_rating_lets_the_trade_open_and_is_recorded(session):
    response = plan(session, FakeLLM(final=final("Buy")))
    assert response.status == "executed"
    assert "AI Committee rated AAPL Buy" in response.auto_execute_note
    assert "AI Committee rated AAPL Buy" in response.signal_reasons
    run = session.exec(select(CommitteeRun)).one()
    assert run.rating == "Buy" and run.status == "done"
    assert session.exec(select(PaperPosition)).first() is not None


def test_objection_cancels_the_trade_but_never_changes_its_levels(session):
    llm = FakeLLM(final=final("Hold"))
    response = plan(session, llm)
    assert response.status == "no_trade"
    assert "does not back a long" in response.reason
    assert response.direction == "long" and response.entry and response.stop  # the rules' numbers, untouched
    assert session.exec(select(PaperPosition)).first() is None


def test_objection_with_hold_action_leaves_the_plan_pending(session):
    response = plan(session, FakeLLM(final=final("Sell")), committee_gate_action="hold")
    assert response.status == "pending"
    assert "held" in response.auto_execute_note and "manual review" in response.auto_execute_note
    assert session.exec(select(PaperPosition)).first() is None


def test_unreadable_rating_neither_approves_nor_objects(session):
    response = plan(session, FakeLLM(final="Honestly I would hold, maybe sell."))
    assert response.status == "executed"
    assert "no readable rating" in response.auto_execute_note


def test_a_failed_committee_never_blocks_a_trade(session):
    response = plan(session, FakeLLM(fail_on="manager"))
    assert response.status == "executed"


def test_no_budget_means_no_gate(session):
    llm = FakeLLM(final=final("Sell"))
    response = plan(session, llm, gate=False)  # a caller that passes no budget (a backtest) never gates
    assert response.status == "executed"
    assert session.exec(select(CommitteeRun)).first() is None


def test_gate_off_means_no_gate(session):
    response = plan(session, FakeLLM(final=final("Sell")), committee_gate_enabled=False)
    assert response.status == "executed"
    assert session.exec(select(CommitteeRun)).first() is None


def test_a_rejected_plan_never_reaches_the_committee(session):
    # Confidence bar above anything this fixture can reach: the rules say no, so no AI call is spent.
    response = plan(session, FakeLLM(final=final("Buy")), min_confidence_for_trade=100)
    assert response.direction is None
    assert session.exec(select(CommitteeRun)).first() is None


def test_scan_allowance_caps_committee_runs(session, monkeypatch):
    monkeypatch.setattr("app.services.automation_service.get_default_watchlist", lambda n: ["AAA", "BBB", "CCC"])
    scan_settings = settings(committee_gate_max_per_scan=2, max_concurrent_positions=5)
    monkeypatch.setattr("app.services.trade_plan_service.load_app_settings", lambda: scan_settings)
    llm = FakeLLM(final=final("Buy"))
    run_auto_scan(scan_settings, GateData(), llm, session)
    assert len(session.exec(select(CommitteeRun)).all()) == 2


def test_the_committee_is_given_the_extra_data_sections(session):
    llm = FakeLLM(final=final("Buy"))
    plan(session, llm)
    prompts = [c["prompt"] for c in llm.calls if c["kind"] == "analyst"]
    assert any("options analyst" in p for p in prompts)
    assert any("smart-money analyst" in p for p in prompts)


# ------------------------------------------------------------------ fingerprint and settings


def test_gate_changes_the_strategy_fingerprint_only_while_on():
    from app.strategy.snapshot import build_snapshot, fingerprint

    off = fingerprint(build_snapshot(AppSettings()))
    assert fingerprint(build_snapshot(AppSettings(committee_gate_action="hold"))) == off  # inert while off
    on = fingerprint(build_snapshot(AppSettings(committee_gate_enabled=True)))
    assert on != off
    assert fingerprint(build_snapshot(AppSettings(committee_gate_enabled=True, committee_gate_action="hold"))) != on


# ------------------------------------------------------------------ a plan you will execute by hand


def test_a_hand_executed_plan_is_still_read_by_the_committee_and_says_so(session):
    response = plan(session, FakeLLM(final=final("Buy")), auto_execute_trade_plans=False)
    assert response.status == "pending"  # auto-execute is off: you decide
    assert response.committee_rating == "Buy" and response.committee_run_id
    assert "AI Committee rated AAPL Buy" in response.auto_execute_note
    assert session.exec(select(PaperPosition)).first() is None


def test_an_objection_cancels_a_hand_executed_plan_too(session):
    response = plan(session, FakeLLM(final=final("Hold")), auto_execute_trade_plans=False)
    assert response.status == "no_trade" and response.committee_rating == "Hold"
    assert "does not back a long" in response.reason


def test_the_rating_is_stored_on_the_plan_and_served_back(session):
    from app.api.routers.trade_plans import trade_plan_to_response
    from app.portfolio.models import TradePlanRecord

    response = plan(session, FakeLLM(final=final("Overweight")))
    record = session.get(TradePlanRecord, response.id)
    assert (record.committee_rating, record.committee_run_id) == ("Overweight", response.committee_run_id)
    again = trade_plan_to_response(record)
    assert again.committee_rating == "Overweight" and again.committee_run_id == response.committee_run_id


def test_with_the_gate_off_a_hand_executed_plan_makes_no_committee_run(session):
    response = plan(session, FakeLLM(final=final("Sell")), auto_execute_trade_plans=False, committee_gate_enabled=False)
    assert response.committee_rating is None and session.exec(select(CommitteeRun)).first() is None


# ------------------------------------------------------------------ the card always says why


def test_every_plan_says_why_the_committee_did_not_read_it(session):
    off = plan(session, FakeLLM(final=final("Buy")), committee_gate_enabled=False)
    assert "gate is off" in off.committee_note
    no_budget = plan(session, FakeLLM(final=final("Buy")), gate=False)
    assert "does not use the committee" in no_budget.committee_note


def test_a_plan_the_committee_read_says_so_in_its_note(session):
    response = plan(session, FakeLLM(final=final("Buy")))
    assert "AI Committee rated AAPL Buy" in response.committee_note


def test_a_manual_generate_after_the_close_is_read_for_information_and_cancels_nothing(session, monkeypatch):
    from app.portfolio.engine import PaperTradingEngine

    monkeypatch.setattr(PaperTradingEngine, "is_market_open_for", lambda self, symbol: False)
    response = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, GateData(), FakeLLM(final=final("Sell")), session,
        settings=settings(), committee_gate=CommitteeGateBudget(1),
    )
    assert response.status == "pending"  # waiting for the open: an objection cancels nothing here
    assert response.committee_rating == "Sell"
    assert "for your information" in response.committee_note


def test_after_the_close_an_automatic_pass_waits_for_the_redo_instead(session, monkeypatch):
    from app.portfolio.engine import PaperTradingEngine

    monkeypatch.setattr(PaperTradingEngine, "is_market_open_for", lambda self, symbol: False)
    response = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, GateData(), FakeLLM(final=final("Buy")), session,
        settings=settings(), committee_gate=CommitteeGateBudget(1), source="auto_scan",
    )
    assert response.committee_rating is None and "09:45" in response.committee_note


def test_a_gate_run_saves_its_progress_step_by_step(session, monkeypatch):
    from app.committee import gate as gate_module
    from app.committee.orchestrator import CommitteeOutcome

    seen = []

    def fake_run(symbol, data_provider, llm, settings, publish, session=None, **kwargs):
        publish([{"key": "analyst_market", "status": "running"}], 1)
        row = session.exec(select(CommitteeRun)).one()
        seen.append((row.status, row.llm_calls_used, json.loads(row.steps)[0]["status"]))  # visible mid-run
        return CommitteeOutcome(steps=[], rating="Buy", calls_used=1)

    monkeypatch.setattr(gate_module, "run_committee", fake_run)
    result = gate_module.run_committee_gate(session, "AAPL", "long", GateData(), FakeLLM(), settings())
    assert seen == [("running", 1, "running")]
    assert result.rating == "Buy" and result.ran


# ------------------------------------------------------------------ for a person who is waiting: in the background


@pytest.fixture
def bg(tmp_path):
    """A file database (the committee thread needs the same data the request sees) and a manager on it."""
    from app.committee.service import CommitteeManager

    engine = create_engine(f"sqlite:///{tmp_path / 'bg.db'}", connect_args={"check_same_thread": False, "timeout": 30})
    SQLModel.metadata.create_all(engine)
    manager = CommitteeManager(lambda: Session(engine))
    budget = CommitteeGateBudget(1, background=True, manager=manager, session_factory=lambda: Session(engine))
    with Session(engine) as session:
        yield session, manager, budget, engine


def bg_plan(session, llm, budget, **overrides):
    return trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, GateData(), llm, session, settings=settings(**overrides), committee_gate=budget
    )


def finished(manager):
    manager._thread.join(timeout=30)


def test_generate_returns_at_once_and_the_committee_reads_in_the_background(bg):
    from app.portfolio.models import TradePlanRecord

    session, manager, budget, engine = bg
    response = bg_plan(session, FakeLLM(final=final("Buy")), budget)
    # The plan is back before the committee has finished: pending, not opened, with its run to watch.
    assert response.status == "pending" and response.committee_run_id
    assert "reading this plan now" in response.committee_note
    assert session.exec(select(PaperPosition)).first() is None
    finished(manager)
    with Session(engine) as fresh:
        record = fresh.get(TradePlanRecord, response.id)
        assert record.committee_rating == "Buy" and record.status == "pending"
        assert "You can execute it" in record.committee_note
        assert fresh.exec(select(PaperPosition)).first() is None  # a plan made for a person is never opened by itself


def test_a_background_objection_cancels_the_plan_when_it_ends(bg):
    from app.portfolio.models import TradePlanRecord

    session, manager, budget, engine = bg
    response = bg_plan(session, FakeLLM(final=final("Hold")), budget)
    finished(manager)
    with Session(engine) as fresh:
        record = fresh.get(TradePlanRecord, response.id)
        assert record.status == "no_trade" and "does not back a long" in record.reason
        assert record.committee_rating == "Hold"


def test_a_background_objection_can_leave_the_plan_for_review(bg):
    from app.portfolio.models import TradePlanRecord

    session, manager, budget, engine = bg
    response = bg_plan(session, FakeLLM(final=final("Sell")), budget, committee_gate_action="hold")
    finished(manager)
    with Session(engine) as fresh:
        record = fresh.get(TradePlanRecord, response.id)
        assert record.status == "pending" and "Left pending for your review" in record.committee_note


def test_a_background_reading_after_the_close_is_for_information_only(bg, monkeypatch):
    from app.portfolio.engine import PaperTradingEngine
    from app.portfolio.models import TradePlanRecord

    session, manager, budget, engine = bg
    monkeypatch.setattr(PaperTradingEngine, "is_market_open_for", lambda self, symbol: False)
    response = bg_plan(session, FakeLLM(final=final("Sell")), budget)
    finished(manager)
    with Session(engine) as fresh:
        record = fresh.get(TradePlanRecord, response.id)
        assert record.committee_rating == "Sell" and record.status == "pending"  # nothing is cancelled after hours
        assert "for your information" in record.committee_note


def test_a_busy_committee_never_blocks_the_plan(bg):
    from app.committee.models import RUN_RUNNING

    session, manager, budget, engine = bg
    session.add(CommitteeRun(symbol="OTHER", status=RUN_RUNNING))
    session.commit()
    response = bg_plan(session, FakeLLM(final=final("Sell")), budget)
    assert response.status == "executed"  # it carried on as if the gate were not there
    assert "another committee run is in progress" in response.committee_note
