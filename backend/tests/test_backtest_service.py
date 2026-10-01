"""Backtest jobs and the /api/backtests routes, on a throwaway database and a throwaway history store."""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_app_settings, get_session
from app.api.routers import backtests as backtests_router
from app.backtest import service
from app.backtest.models import BacktestEquityPoint, BacktestRun, BacktestTrade
from app.backtest.runner import BacktestResult
from app.backtest.service import BacktestManager, recover_interrupted_runs
from app.config import AppSettings
from app.data_providers.history_store import HistoryStore
from app.main import app
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.strategy.models import StrategyVersion
from tests.backtest_helpers import standard_book, trading_days_from, wiggly_uptrend

DAYS = trading_days_from(date(2024, 1, 2), 330)
START, END = DAYS[250].isoformat(), DAYS[270].isoformat()
# These tests are about the run itself; the random-entry baseline has its own tests (test_backtest_baseline.py).
BODY = {"symbols": ["AAA"], "start": START, "end": END, "run_baseline": False}


@pytest.fixture()
def env(tmp_path):
    from app.knowledge import models as _k  # noqa: F401
    from app.portfolio import models as _p  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    store = HistoryStore(tmp_path / "history.db")
    book = standard_book(DAYS, {"AAA": wiggly_uptrend(330, daily=0.01, dip=-0.01)})
    now = datetime(2025, 6, 1, tzinfo=timezone.utc)
    for symbol, series in book.series.items():
        store._upsert(symbol, series.frame, "yfinance", now, covered_from=DAYS[0])

    manager = BacktestManager(lambda: Session(engine), lambda: store)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[backtests_router.get_manager] = lambda: manager
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_app_settings] = lambda: AppSettings()
    yield TestClient(app), manager, engine
    manager.wait(30)
    app.dependency_overrides.clear()
    store.close()


def post(client, **extra):
    backtests_router._last_backtest_start_monotonic = None  # the cooldown has its own test
    return client.post("/api/backtests", json={**BODY, **extra})


def test_a_run_goes_from_queued_to_done_with_trades_equity_and_summary(env):
    client, manager, engine = env
    response = post(client)
    assert response.status_code == 202
    run_id = response.json()["id"]
    manager.wait(60)

    run = client.get(f"/api/backtests/{run_id}").json()
    assert run["status"] == "done" and run["error"] is None
    assert run["progress"]["days_done"] == run["progress"]["days_total"] > 0
    assert run["started_at"] and run["finished_at"]
    assert run["strategy_fingerprint"] and len(run["strategy_fingerprint"]) >= 16
    summary = run["summary"]
    assert summary["trade_count"] >= 1 and summary["days_simulated"] == 21
    assert summary["symbols_with_data"] == 1 and summary["symbols_skipped"] == []
    assert run["params"]["symbols"] == ["AAA"] and run["params"]["start"] == START
    assert run["params"]["history"]["AAA"]["bars"] == 271  # up to the run's last day
    assert run["coverage"]["profile"] == "price_only" and run["coverage"]["achievable_points"] == 7

    trades = client.get(f"/api/backtests/{run_id}/trades").json()
    assert len(trades) >= 1
    first = trades[0]
    assert first["symbol"] == "AAA" and first["direction"] == "long" and first["confidence_points"] == 5
    assert first["scores"]["technical_score"] + first["scores"]["market_confirmation_score"] == 5

    equity = client.get(f"/api/backtests/{run_id}/equity").json()
    assert len(equity) == summary["days_simulated"]
    assert equity[-1]["equity"] == pytest.approx(summary["final_equity"])

    listed = client.get("/api/backtests").json()
    assert [r["id"] for r in listed] == [run_id]


def test_a_run_writes_only_the_backtest_tables(env):
    client, manager, engine = env
    post(client)
    manager.wait(60)
    with Session(engine) as session:
        assert session.exec(select(BacktestTrade)).first() is not None
        assert session.exec(select(BacktestEquityPoint)).first() is not None
        # the simulated plans, positions and strategy versions lived in the run's own throwaway database
        assert session.exec(select(TradePlanRecord)).first() is None
        assert session.exec(select(PaperPosition)).first() is None
        assert session.exec(select(StrategyVersion)).first() is None


def test_overrides_are_recorded_and_the_bar_is_reported_beside_the_live_one(env):
    client, manager, _ = env
    run_id = post(client, overrides={"min_confidence_for_trade": 20, "slippage_bps": 0}, decision_every_n_days=2).json()["id"]
    manager.wait(60)
    run = client.get(f"/api/backtests/{run_id}").json()
    assert run["params"]["overrides"] == {"min_confidence_for_trade": 20, "slippage_bps": 0.0}
    assert run["params"]["decision_every_n_days"] == 2
    assert run["params"]["effective_settings"]["min_confidence_for_trade"] == 20
    assert run["coverage"]["min_confidence_for_trade"] == 20
    assert run["coverage"]["live_min_confidence_for_trade"] == 30
    assert run["coverage"]["bar_points_needed"] == 4 and run["coverage"]["live_bar_points_needed"] == 5


def test_only_whitelisted_settings_can_be_overridden_and_crypto_is_rejected(env):
    client, _, _ = env
    assert post(client, overrides={"llm_provider": "openai"}).status_code == 422
    assert post(client, overrides={"slippage_bps": -1}).status_code == 422
    assert post(client, symbols=["BTC-USD"]).status_code == 422
    assert post(client, symbols=[]).status_code == 422
    assert post(client, decision_every_n_days=0).status_code == 422
    assert post(client, start=END, end=START).status_code == 400


def test_missing_history_is_a_clear_400(env, tmp_path):
    client, manager, _ = env
    manager._store_getter = lambda: HistoryStore(tmp_path / "empty.db")
    response = post(client)
    assert response.status_code == 400
    assert "preload_history" in response.json()["detail"] and "SPY" in response.json()["detail"]


def test_a_symbol_without_history_is_reported_not_fatal(env):
    client, manager, _ = env
    run_id = post(client, symbols=["AAA", "ZZZ"]).json()["id"]
    manager.wait(60)
    summary = client.get(f"/api/backtests/{run_id}").json()["summary"]
    assert summary["symbols_skipped"] == [{"symbol": "ZZZ", "reason": "no stored price history"}]


def test_only_one_run_at_a_time_and_cancel_keeps_the_partial_run(env, monkeypatch):
    client, manager, _ = env
    started = threading.Event()

    def slow_run(params, settings, book, *, progress=None, should_cancel=None, provider=None):
        started.set()
        while not should_cancel():
            time.sleep(0.01)
        return BacktestResult(
            status="cancelled", trades=[], equity=[],
            summary={"days_simulated": 0, "days_requested": 5, "cancelled": True}, coverage={"profile": "price_only"},
        )

    monkeypatch.setattr(service, "run_backtest", slow_run)
    run_id = post(client).json()["id"]
    assert started.wait(10)
    assert client.get(f"/api/backtests/{run_id}").json()["status"] == "running"

    second = post(client)
    assert second.status_code == 409 and "already running" in second.json()["detail"]

    assert client.post(f"/api/backtests/{run_id}/cancel").json() == {"id": run_id, "status": "cancelling"}
    manager.wait(10)
    run = client.get(f"/api/backtests/{run_id}").json()
    assert run["status"] == "cancelled" and run["cancel_requested"] is True
    assert client.post(f"/api/backtests/{run_id}/cancel").json()["status"] == "cancelled"  # nothing left to cancel
    assert post(client).status_code == 202  # the slot is free again
    manager.cancel(2)
    manager.wait(10)


def test_starting_runs_back_to_back_is_throttled(env):
    client, manager, _ = env
    backtests_router._last_backtest_start_monotonic = None
    assert client.post("/api/backtests", json=BODY).status_code == 202
    manager.wait(60)
    second = client.post("/api/backtests", json=BODY)
    assert second.status_code == 429


def test_unknown_runs_are_404(env):
    client, _, _ = env
    for url in ("/api/backtests/99", "/api/backtests/99/trades", "/api/backtests/99/equity"):
        assert client.get(url).status_code == 404
    assert client.post("/api/backtests/99/cancel").status_code == 404


def test_a_run_left_running_by_a_dead_process_is_marked_failed_on_startup(env):
    _, _, engine = env
    with Session(engine) as session:
        session.add(BacktestRun(status="running"))
        session.add(BacktestRun(status="queued"))
        session.add(BacktestRun(status="done"))
        session.commit()
    assert recover_interrupted_runs(lambda: Session(engine)) == 2
    with Session(engine) as session:
        statuses = sorted(r.status for r in session.exec(select(BacktestRun)).all())
        assert statuses == ["done", "failed", "failed"]
        failed = session.exec(select(BacktestRun).where(BacktestRun.status == "failed")).first()
        assert "interrupted" in failed.error and failed.finished_at is not None


def test_the_router_is_behind_the_auth_gate():
    from app.api.deps import require_auth

    assert any(dep.dependency is require_auth for dep in backtests_router.router.dependencies)
