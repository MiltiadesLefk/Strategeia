"""Smart-money candidates routed through the hold buy (PORTOFOLIO_BUILDER step 3)."""
from __future__ import annotations

import pytest
from sqlmodel import select

from app.analysis.quality_gate import GateResult
from app.portfolio.models import PaperPosition
from app.services import hold_buy_service
from app.services import smart_money_pipeline_service as pipeline
from app.smartmoney.models import STATUS_AUTO_BOUGHT, STATUS_BOUGHT, STATUS_NEEDS_REVIEW
from tests.test_automation_service import FakeUniverseProvider
from tests.test_smart_money_pipeline import add_candidate, engine, evaluate, sent, session, settings_  # noqa: F401
from app.llm_providers.null_provider import NullLLMProvider

pytestmark = pytest.mark.usefixtures("sent")


def gate(passed: bool):
    from app.analysis.quality_gate import GateCheck

    checks = [GateCheck("trend", passed, "below the 200-day average")]
    return lambda dp, symbol: GateResult(passed, checks)


def test_a_passed_gate_hold_buys_in_the_auto_sleeve(session, monkeypatch):
    monkeypatch.setattr(hold_buy_service, "check_quality", gate(True))
    row = add_candidate(session)
    assert evaluate(session, row, settings=settings_()) == STATUS_AUTO_BOUGHT
    position = session.exec(select(PaperPosition)).one()
    assert (position.mode, position.tp1, position.sleeve_id is not None) == ("hold", 0.0, True)


def test_a_failed_gate_still_buys_at_half_size(session, monkeypatch):
    monkeypatch.setattr(hold_buy_service, "check_quality", gate(False))
    full = {}
    monkeypatch.setattr(hold_buy_service, "hold_shares", lambda eq, w, price: full.setdefault("w", w) and 1)
    row = add_candidate(session)
    assert evaluate(session, row, settings=settings_()) == STATUS_AUTO_BOUGHT
    assert full["w"] == settings_().hold_target_weight_pct * 0.5
    position = session.exec(select(PaperPosition)).one()
    assert position.mode == "hold"


def test_review_buy_skips_the_gate_but_not_the_engine(session, monkeypatch):
    monkeypatch.setattr(hold_buy_service, "check_quality", gate(False))
    settings = settings_(hold_max_positions=1)
    row = add_candidate(session)
    row.status = STATUS_NEEDS_REVIEW
    session.add(row)
    session.commit()
    position = pipeline.execute_review_buy(session, row, settings, FakeUniverseProvider(), NullLLMProvider())
    assert position.mode == "hold"
    session.refresh(row)
    assert row.status == STATUS_BOUGHT
    with pytest.raises(pipeline.ReviewBuyRefused):  # the engine's duplicate rule still applies
        pipeline.execute_review_buy(session, row, settings, FakeUniverseProvider(), NullLLMProvider())


def test_the_morning_watchers_stay_out_of_the_all_day_tick(session):
    from app.watchers import runner

    class W:
        poll_interval_seconds = 60

        def __init__(self, name):
            self.name = name

    ran = []
    runner.run_watcher_once = lambda s, w, *a, **k: ran.append(w.name) or w.name  # noqa: E731
    try:
        runner.run_due_watchers(session, settings_(watchers_enabled=True), watchers=[W("sec_filings"), W("fed")])
        assert ran == ["fed"]
    finally:
        import importlib

        importlib.reload(runner)
