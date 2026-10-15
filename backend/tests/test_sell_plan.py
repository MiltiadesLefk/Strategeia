"""Sell plan and rebuy watch (F4): the signal counting, the stop raise, and the watch lifecycle."""
from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis.quality_gate import evaluate_quality_gate
from app.portfolio.models import PaperPosition, RebuyWatch
from app.services.sell_plan_service import _update_rebuy_watch, days_under_ma, total_signals, trail_stop
from app.timeutil import utcnow_naive


def test_signal_rules():
    assert total_signals(0, True) == 0  # news never counts alone
    assert total_signals(1, True) == 2
    assert days_under_ma([100.0] * 200 + [50.0] * 5, 5)
    assert not days_under_ma([100.0] * 204 + [50.0], 5)
    assert trail_stop(100, 130, 140, 10, 75) == 126
    assert trail_stop(100, 130, 140, 0, 75) == 100  # breakeven
    assert trail_stop(100, 95, 110, 10, 75) is None  # not in profit


def test_rebuy_gate_drops_only_the_200_day_rule():
    falling = [100.0 - i * 0.1 for i in range(250)]
    assert not evaluate_quality_gate(70.0, falling, None, []).checks[0].ok
    assert all(c.name != "trend" for c in evaluate_quality_gate(70.0, falling, None, [], require_trend=False).checks)


def test_hold_review_once_per_period(monkeypatch):
    from datetime import timedelta
    from types import SimpleNamespace

    from app.analysis.quality_gate import GateCheck, GateResult
    from app.config import AppSettings
    from app.services import sell_plan_service as sp

    msgs: list[str] = []
    monkeypatch.setattr(sp, "notify", lambda *a: msgs.append(a[2]))
    monkeypatch.setattr(sp, "check_quality", lambda *a, **k: GateResult(False, [GateCheck("trend", True, ""), GateCheck("profit", False, "")]))
    provider = SimpleNamespace(get_quote=lambda _s: SimpleNamespace(price=110.0))
    e = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(e)
    with Session(e) as s:
        s.add(PaperPosition(symbol="NOVA", direction="long", entry_price=100, stop_loss=75, tp1=0, tp2=0, shares=1, mode="hold",
                            opened_at=utcnow_naive() - timedelta(days=91)))
        s.commit()
        assert sp.run_hold_reviews(AppSettings(hold_review_days=0), provider, s) == []
        assert sp.run_hold_reviews(AppSettings(), provider, s) == ["NOVA"]
        assert sp.run_hold_reviews(AppSettings(), provider, s) == []
        assert len(msgs) == 1 and "+10.0%" in msgs[0] and "Fails: profit" in msgs[0]
        assert s.exec(select(PaperPosition)).one().last_reviewed_at is not None


def test_watch_lifecycle():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(e)
    with Session(e) as s:
        def pos(reason):
            p = PaperPosition(symbol="NOVA", direction="long", entry_price=100, stop_loss=75, tp1=0, tp2=0, shares=1, mode="hold",
                              status="closed", closed_at=utcnow_naive(), close_price=70, close_reason=reason)
            s.add(p)
            s.commit()
            s.refresh(p)
            return p

        first = pos("disaster_stop")
        _update_rebuy_watch(s)
        watch = s.exec(select(RebuyWatch)).one()
        assert (watch.sold_position_id, watch.status) == (first.id, "watching")
        second = pos("disaster_stop")  # the rebuy, stopped again
        watch.status, watch.rebuy_position_id = "rebought", second.id
        s.add(watch)
        s.commit()
        _update_rebuy_watch(s)
        assert s.exec(select(RebuyWatch)).one().status == "dropped"
