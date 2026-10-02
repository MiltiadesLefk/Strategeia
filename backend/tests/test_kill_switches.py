"""Kill switches and drift alarms: a paused sleeve opens nothing, closes nothing,
and stays paused until resumed. In-memory database, no network."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.backtest.models import RUN_DONE, BacktestRun, BacktestTrade
from app.config import AppSettings
from app.portfolio.drift import psi, psi_band
from app.portfolio.engine import SleeveDisabledError, SleevePausedError
from app.portfolio.kill_switch_models import SleevePause
from app.portfolio.models import EquitySnapshot, PaperPosition
from app.portfolio.sleeves import ensure_core_sleeve
from app.services.kill_switch_service import active_pause, evaluate_kill_switches, resume_sleeve
from tests.test_sleeves import engine_for, new_sleeve, plan

T0 = datetime(2026, 10, 5)  # after the pinned test clock, so it follows any snapshot the engine wrote


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def curve(session, sleeve_id, values, start=T0):
    for i, v in enumerate(values):
        session.add(EquitySnapshot(timestamp=start + timedelta(days=i), equity_value=v, cash_balance=v, sleeve_id=sleeve_id))
    session.commit()


ON = AppSettings(kill_switch_enabled=True, kill_switch_drawdown_pct=10.0)


def test_psi_is_zero_for_the_same_spread_and_large_for_a_shifted_one():
    ref = [(-1 + 0.1 * i) for i in range(40)]
    assert psi(ref, ref) == pytest.approx(0, abs=1e-6)
    shifted = [v + 5 for v in ref]
    assert psi_band(psi(ref, shifted)) == "critical"
    assert psi(ref, [1.0]) is None


def test_off_by_default_nothing_happens(session):
    core = ensure_core_sleeve(session)
    curve(session, core.id, [100, 50])
    assert evaluate_kill_switches(session, AppSettings()) == []


def test_drawdown_pauses_and_alerts_once(session):
    sleeve = new_sleeve(session)
    curve(session, sleeve.id, [100, 120, 100])  # 16.7% below the peak
    sent = []
    made = evaluate_kill_switches(session, ON, sent.append)
    assert [p.sleeve_key for p in made] == [sleeve.key] and made[0].reason == "drawdown" and made[0].alert_sent
    assert evaluate_kill_switches(session, ON, sent.append) == []  # already paused: no second alert
    assert len(sent) == 1


def test_a_recovered_dip_does_not_pause(session):
    sleeve = new_sleeve(session)
    curve(session, sleeve.id, [100, 70, 120])
    assert evaluate_kill_switches(session, ON) == []


def test_paused_sleeve_refuses_new_positions_but_keeps_its_open_ones(session):
    sleeve = new_sleeve(session)
    eng = engine_for(session, sleeve)
    eng.open_position(plan(session, "AAPL", sleeve.id))
    curve(session, sleeve.id, [50_000, 60_000, 50_000])  # the sleeve starts with 50,000
    evaluate_kill_switches(session, ON)
    with pytest.raises(SleevePausedError) as exc:
        eng.open_position(plan(session, "MSFT", sleeve.id))
    assert isinstance(exc.value, SleeveDisabledError)
    assert [p.symbol for p in session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all()] == ["AAPL"]
    # another sleeve is untouched
    other = new_sleeve(session, "Other")
    engine_for(session, other).open_position(plan(session, "NVDA", other.id))


def test_resume_lifts_the_pause_and_restarts_the_peak(session):
    sleeve = new_sleeve(session)
    curve(session, sleeve.id, [100, 120, 100], start=datetime(2026, 9, 1))  # before the resume
    evaluate_kill_switches(session, ON)
    assert resume_sleeve(session, sleeve.key) and active_pause(session, sleeve.key) is None
    assert not resume_sleeve(session, sleeve.key)
    # the old peak (120) predates the resume, so the same curve does not trip again
    assert evaluate_kill_switches(session, ON) == []
    assert session.exec(select(SleevePause)).all()[0].resolved_at is not None


def _closed(session, r_values):
    for i, r in enumerate(r_values):
        session.add(
            PaperPosition(
                symbol=f"S{i}", direction="long", entry_price=100, stop_loss=90, tp1=120, tp2=130, shares=1,
                status="closed", realized_r=r, realized_pnl=r, opened_at=T0, closed_at=T0,
            )
        )
    session.commit()


def test_drift_from_the_backtest_pauses_core_only_with_enough_trades(session):
    run = BacktestRun(status=RUN_DONE)
    session.add(run)
    session.commit()
    for i in range(40):
        session.add(
            BacktestTrade(
                run_id=run.id, symbol="X", direction="long", entry_at=T0, entry_date=T0.date(),
                entry_price=100, stop_loss=90, tp1=120, shares=1, realized_r=-1 + 0.1 * i,
            )
        )
    session.commit()
    settings = AppSettings(kill_switch_enabled=True, kill_switch_drawdown_pct=0, kill_switch_drift_psi=0.25)
    _closed(session, [-1 + 0.1 * i for i in range(10)])  # too few live trades
    assert evaluate_kill_switches(session, settings) == []
    _closed(session, [3.0] * 25)  # now plenty, and nothing like the backtest
    made = evaluate_kill_switches(session, settings)
    assert len(made) == 1 and made[0].reason == "drift" and made[0].sleeve_key == "core"
