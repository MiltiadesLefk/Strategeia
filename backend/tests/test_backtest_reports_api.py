"""The statistics endpoints and the baseline phase of a run, through the API on throwaway storage."""

from __future__ import annotations

import json
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
from app.backtest.benchmarks import store_loader
from app.backtest.models import BacktestEquityPoint, BacktestRun, BacktestTrade
from app.backtest.service import BacktestManager
from app.config import AppSettings
from app.data_providers.history_store import HistoryStore
from app.main import app
from tests.backtest_helpers import standard_book, trading_days_from, wiggly_uptrend

DAYS = trading_days_from(date(2024, 1, 2), 330)
START, END = DAYS[250].isoformat(), DAYS[280].isoformat()
SYMBOLS = ["AAA", "BBB", "CCC"]
BODY = {"symbols": SYMBOLS, "start": START, "end": END, "baseline_runs": 3}
BACKTEST_TABLES = (BacktestRun, BacktestTrade, BacktestEquityPoint)


@pytest.fixture()
def env(tmp_path):
    from app.knowledge import models as _k  # noqa: F401
    from app.portfolio import models as _p  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    store = HistoryStore(tmp_path / "history.db")
    closes = wiggly_uptrend(330, daily=0.01, dip=-0.01)
    book = standard_book(DAYS, {name: closes * (1 + 0.1 * i) for i, name in enumerate(SYMBOLS)})
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
    app.dependency_overrides[backtests_router.get_bars_loader] = lambda: store_loader(store)
    app.dependency_overrides[backtests_router.get_coverage_store] = lambda: store
    yield TestClient(app), manager, engine, store
    manager.wait(60)
    app.dependency_overrides.clear()
    store.close()


def post(client, **extra):
    backtests_router._last_backtest_start_monotonic = None
    return client.post("/api/backtests", json={**BODY, **extra})


def finished_run(env, **extra) -> int:
    client, manager, *_ = env
    response = post(client, **extra)
    assert response.status_code == 202, response.text
    manager.wait(120)
    return response.json()["id"]


def dump(engine) -> dict:
    with Session(engine) as session:
        return {model.__name__: [row.model_dump() for row in session.exec(select(model)).all()] for model in BACKTEST_TABLES}


# ------------------------------------------------------------------ the request


def test_the_baseline_request_fields_are_validated(env):
    client, *_ = env
    assert post(client, baseline_runs=51).status_code == 422
    assert post(client, baseline_runs=-1).status_code == 422
    assert post(client, run_baseline="maybe").status_code == 422


def test_a_run_with_the_baseline_ends_done_with_every_seed_stored_and_the_phase_cleared(env):
    client, manager, engine, _ = env
    run_id = finished_run(env)
    run = client.get(f"/api/backtests/{run_id}").json()
    assert run["status"] == "done" and run["error"] is None and run["finished_at"]
    assert run["progress"]["phase"] is None
    assert run["progress"]["baseline_seeds_done"] == run["progress"]["baseline_seeds_total"] == 3
    assert run["params"]["run_baseline"] is True and run["params"]["baseline_runs"] == 3
    with Session(engine) as session:
        stored = json.loads(session.get(BacktestRun, run_id).baseline_json)
    assert stored["status"] == "done" and [s["seed"] for s in stored["seeds"]] == [1, 2, 3]
    assert stored["entry_probability"] == pytest.approx(run["summary"]["plans"] / run["summary"]["evaluations"])


def test_a_run_can_skip_the_baseline(env):
    client, *_ = env
    run_id = finished_run(env, run_baseline=False)
    run = client.get(f"/api/backtests/{run_id}").json()
    assert run["status"] == "done" and run["progress"]["baseline_seeds_total"] is None
    baseline = client.get(f"/api/backtests/{run_id}/baseline").json()
    assert baseline["available"] is False and "without a random-entry baseline" in baseline["reason"]
    zero = finished_run(env, baseline_runs=0)
    assert client.get(f"/api/backtests/{zero}/baseline").json()["available"] is False


# ------------------------------------------------------------------ the statistics endpoints


def test_metrics_agree_with_the_runs_own_summary(env):
    client, *_ = env
    run_id = finished_run(env)
    summary = client.get(f"/api/backtests/{run_id}").json()["summary"]
    metrics = client.get(f"/api/backtests/{run_id}/metrics").json()
    assert metrics["returns"]["total_return_pct"] == pytest.approx(summary["total_return_pct"])
    assert metrics["drawdown"]["max_drawdown_pct"] == pytest.approx(summary["max_drawdown_pct"])
    assert metrics["trades"]["closed_trades"] == summary["trade_count"] >= 1
    assert metrics["trades"]["win_rate_pct"] == pytest.approx(summary["win_rate"] * 100)
    assert metrics["trades"]["average_r"] == pytest.approx(summary["average_r"])
    assert metrics["period"]["trading_days"] == summary["days_simulated"] == len(metrics["drawdown_series"])
    assert metrics["conventions"]["trading_days_per_year"] == 252 and metrics["conventions"]["risk_free_rate"] == 0
    assert metrics["exit_reasons"] and metrics["by_direction"] and metrics["monthly_returns"] and metrics["yearly_returns"]


def test_benchmarks_cover_exactly_the_runs_days_from_the_same_capital(env):
    client, *_ = env
    run_id = finished_run(env)
    equity = client.get(f"/api/backtests/{run_id}/equity").json()
    out = client.get(f"/api/backtests/{run_id}/benchmarks").json()
    assert out["available"] and out["starting_capital"] == 100_000.0
    assert [p["day"] for p in out["spy"]["equity"]] == [p["day"] for p in equity]
    assert out["comparison"]["days_compared"] == len(equity) and out["comparison"]["beta"] is not None
    assert out["equal_weight"]["available"] and out["equal_weight"]["survivor_biased"] is True
    assert out["equal_weight"]["symbols_used"] == SYMBOLS


def test_the_baseline_endpoint_places_the_real_run_among_the_random_ones(env):
    client, *_ = env
    run_id = finished_run(env)
    out = client.get(f"/api/backtests/{run_id}/baseline").json()
    assert out["available"] and len(out["seeds"]) == 3 and out["status"] == "done"
    placement = out["placement"]["total_return_pct"]
    assert placement["n"] == 3 and 0 <= placement["percentile"] <= 100 and placement["chance_random_matches"] >= 1 / 4
    assert out["enough_seeds"] is False and "Only 3 random runs" in out["caveat"]
    assert out["real"]["total_return_pct"] == pytest.approx(client.get(f"/api/backtests/{run_id}/metrics").json()["returns"]["total_return_pct"])


def test_the_scorecard_endpoint_takes_criteria_and_reports_each_line(env):
    client, *_ = env
    run_id = finished_run(env)
    card = client.get(f"/api/backtests/{run_id}/scorecard", params={"min_trades": 1, "max_drawdown_pct": 50}).json()
    assert card["criteria"]["min_trades"] == 1 and card["criteria"]["max_drawdown_pct"] == 50
    keys = [c["key"] for c in card["checks"]]
    assert keys == ["trades", "after_costs", "years", "drawdown", "beats_spy_return", "beats_spy_risk_adjusted", "beats_baseline"]
    lines = {c["key"]: c for c in card["checks"]}
    assert lines["trades"]["status"] == "pass"
    assert lines["years"]["status"] == "insufficient_data"  # a few weeks is not a calendar year
    assert lines["beats_baseline"]["status"] == "insufficient_data"  # 3 random runs
    assert {b["key"] for b in card["banners"]} >= {"survivorship", "price_only", "sample_size"}
    assert "Price-only" in next(b for b in card["banners"] if b["key"] == "price_only")["text"]
    assert client.get(f"/api/backtests/{run_id}/scorecard", params={"max_drawdown_pct": 0}).status_code == 422
    assert client.get(f"/api/backtests/{run_id}/scorecard", params={"baseline_percentile": 101}).status_code == 422


def test_the_statistics_endpoints_only_read(env):
    client, _, engine, _ = env
    run_id = finished_run(env)
    before = dump(engine)
    for suffix in ("metrics", "benchmarks", "baseline", "scorecard"):
        assert client.get(f"/api/backtests/{run_id}/{suffix}").status_code == 200
    assert client.get("/api/backtests/history-coverage", params={"symbols": "AAA"}).status_code == 200
    assert dump(engine) == before


def test_unknown_runs_are_404_and_runs_without_results_are_409(env):
    client, _, engine, _ = env
    for suffix in ("metrics", "benchmarks", "baseline", "scorecard"):
        assert client.get(f"/api/backtests/99/{suffix}").status_code == 404
    with Session(engine) as session:
        session.add(BacktestRun(status="failed", error="boom"))
        session.commit()
        run_id = session.exec(select(BacktestRun)).first().id
    for suffix in ("metrics", "benchmarks", "baseline", "scorecard"):
        response = client.get(f"/api/backtests/{run_id}/{suffix}")
        assert response.status_code == 409 and "no results" in response.json()["detail"] and "boom" in response.json()["detail"]


def test_benchmarks_degrade_when_spy_history_is_gone(env):
    client, _, _, store = env
    run_id = finished_run(env)
    store.delete("SPY")
    out = client.get(f"/api/backtests/{run_id}/benchmarks").json()
    assert out["available"] is False and out["comparison"] is None and "SPY" in out["spy"]["reason"]
    card = client.get(f"/api/backtests/{run_id}/scorecard").json()
    lines = {c["key"]: c["status"] for c in card["checks"]}
    assert lines["beats_spy_return"] == lines["beats_spy_risk_adjusted"] == "insufficient_data"
    assert client.get(f"/api/backtests/{run_id}/metrics").status_code == 200  # the rest still works


# ------------------------------------------------------------------ progress and cancelling during the baseline


def test_progress_shows_the_baseline_phase_and_results_are_readable_meanwhile(env, monkeypatch):
    client, manager, engine, _ = env
    in_baseline = threading.Event()
    real_baseline = service.run_baseline

    def slow_baseline(params, settings, book, summary, *, runs, progress, on_seed_done, should_cancel):
        progress(2, runs, 5, 10)  # "seed 2 of K, 5 of 10 days"
        in_baseline.set()
        while not should_cancel():
            time.sleep(0.01)
        return {"requested_runs": runs, "seeds": [], "status": "cancelled", "entry_probability": 0.1}

    monkeypatch.setattr(service, "run_baseline", slow_baseline)
    run_id = post(client).json()["id"]
    assert in_baseline.wait(60)

    run = client.get(f"/api/backtests/{run_id}").json()
    assert run["status"] == "running"
    assert run["progress"]["phase"] == "baseline"
    assert run["progress"]["baseline_seeds_total"] == 3 and run["progress"]["baseline_seeds_done"] == 1
    assert run["progress"]["days_done"] == 5 and run["progress"]["days_total"] == 10
    # the main run is finished and saved, so its statistics can already be read
    assert client.get(f"/api/backtests/{run_id}/metrics").status_code == 200
    assert client.get(f"/api/backtests/{run_id}/trades").json()

    assert client.post(f"/api/backtests/{run_id}/cancel").json()["status"] == "cancelling"
    manager.wait(60)
    monkeypatch.setattr(service, "run_baseline", real_baseline)


def test_cancelling_during_the_baseline_keeps_the_finished_main_run_and_the_seeds_done(env, monkeypatch):
    client, manager, engine, _ = env
    real_baseline = service.run_baseline
    seen = {"days": 0}

    def cancelling_baseline(params, settings, book, summary, *, runs, progress, on_seed_done, should_cancel):
        def noting(seed, total_seeds, done, total):
            seen["days"] += 1
            progress(seed, total_seeds, done, total)

        # the user presses Cancel once the first seed has been saved
        def cancel_after_first(record):
            on_seed_done(record)
            manager.cancel(run_id_holder["id"])

        return real_baseline(params, settings, book, summary, runs=runs, progress=noting, on_seed_done=cancel_after_first, should_cancel=should_cancel)

    run_id_holder = {}
    monkeypatch.setattr(service, "run_baseline", cancelling_baseline)
    run_id = post(client, baseline_runs=10).json()["id"]
    run_id_holder["id"] = run_id
    manager.wait(120)

    run = client.get(f"/api/backtests/{run_id}").json()
    assert run["status"] == "done" and run["cancel_requested"] is True and run["progress"]["phase"] is None
    baseline = client.get(f"/api/backtests/{run_id}/baseline").json()
    assert baseline["status"] == "cancelled" and baseline["requested_runs"] == 10
    assert 1 <= len(baseline["seeds"]) < 10
    # the real run is whole: summary, trades, equity and statistics are all there
    assert run["summary"]["cancelled"] is False and run["summary"]["days_simulated"] == run["summary"]["days_requested"]
    assert client.get(f"/api/backtests/{run_id}/metrics").json()["partial"] is False
    assert len(client.get(f"/api/backtests/{run_id}/equity").json()) == run["summary"]["days_simulated"]


def test_a_baseline_that_crashes_leaves_the_real_run_intact(env, monkeypatch):
    client, manager, *_ = env

    def broken(*args, **kwargs):
        raise RuntimeError("seed exploded")

    monkeypatch.setattr(service, "run_baseline", broken)
    run_id = post(client).json()["id"]
    manager.wait(60)
    run = client.get(f"/api/backtests/{run_id}").json()
    assert run["status"] == "done" and run["error"] is None
    baseline = client.get(f"/api/backtests/{run_id}/baseline").json()
    assert baseline["status"] == "failed" and "seed exploded" in baseline["note"]
    assert client.get(f"/api/backtests/{run_id}/metrics").json()["trades"]["closed_trades"] >= 1


# ------------------------------------------------------------------ what history is held


def test_history_coverage_lists_what_is_stored_and_the_exact_preload_command(env):
    client, _, _, store = env
    out = client.get("/api/backtests/history-coverage", params={"symbols": "aaa, ZZZ, NEWCO"}).json()
    by_symbol = {row["symbol"]: row for row in out["symbols"]}
    assert by_symbol["AAA"]["stored"] is True and by_symbol["AAA"]["bars"] == 330
    assert by_symbol["AAA"]["first_date"] == DAYS[0].isoformat() and by_symbol["AAA"]["last_date"] == DAYS[-1].isoformat()
    assert by_symbol["ZZZ"]["stored"] is False and out["missing"] == ["ZZZ", "NEWCO"]
    assert out["preload_command"] == "python scripts/preload_history.py ZZZ NEWCO --benchmarks"
    assert {b["symbol"] for b in out["benchmarks"]} == {"SPY", "^VIX"} and out["benchmarks_missing"] == []
    assert out["latest_end_date"] == DAYS[-1].isoformat()
    assert "252" in out["warmup_note"]

    complete = client.get("/api/backtests/history-coverage", params={"symbols": "AAA"}).json()
    assert complete["missing"] == [] and complete["preload_command"] is None

    store.delete("^VIX")
    gone = client.get("/api/backtests/history-coverage", params={"symbols": "AAA"}).json()
    assert gone["benchmarks_missing"] == ["^VIX"] and gone["latest_end_date"] is None
    assert gone["preload_command"] == "python scripts/preload_history.py --benchmarks"


def test_history_coverage_with_no_history_file_at_all(env):
    client, *_ = env
    app.dependency_overrides[backtests_router.get_coverage_store] = lambda: None
    out = client.get("/api/backtests/history-coverage", params={"symbols": "AAA"}).json()
    assert out["missing"] == ["AAA"] and len(out["benchmarks_missing"]) == 2 and out["latest_end_date"] is None
    assert out["preload_command"] == "python scripts/preload_history.py AAA --benchmarks"


def test_a_very_long_symbol_list_points_at_the_bundled_list_instead_of_a_huge_command(env):
    client, *_ = env
    names = ",".join(f"S{i}" for i in range(60))
    command = client.get("/api/backtests/history-coverage", params={"symbols": names}).json()["preload_command"]
    assert "--sp500" in command and "S29" in command and "S30" not in command


def test_the_new_routes_sit_behind_the_auth_gate():
    from app.api.deps import require_auth

    assert any(dep.dependency is require_auth for dep in backtests_router.router.dependencies)
    paths = {route.path for route in backtests_router.router.routes}
    assert {"/api/backtests/history-coverage", "/api/backtests/{run_id}/metrics", "/api/backtests/{run_id}/benchmarks",
            "/api/backtests/{run_id}/baseline", "/api/backtests/{run_id}/scorecard"} <= paths
