"""Walk-forward validation: fold layout, in-sample selection, trial counting, the deflated
Sharpe block, cancel, no look-ahead and the endpoints. Window runs are faked where the test is
about the logic around them and real (on a hand-built price book) where it is about data."""

from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.deps import get_app_settings, get_session
from app.api.routers import backtests as backtests_router
from app.backtest import service, validation as v
from app.backtest.calendar import trading_days
from app.backtest.params import BacktestInputError
from app.backtest.runner import BacktestResult
from app.backtest.service import BacktestManager
from app.backtest.validation_models import BacktestValidation
from app.config import AppSettings
from app.data_providers.history_store import HistoryStore
from app.main import app
from tests.backtest_helpers import standard_book, trading_days_from, wiggly_uptrend

DAYS = trading_days_from(date(2024, 1, 2), 330)


# ---------------------------------------------------------------- folds


@pytest.mark.parametrize("mode", ["rolling", "anchored"])
def test_folds_do_not_overlap_and_respect_the_embargo(mode):
    days = trading_days_from(date(2022, 1, 3), 700)
    folds = v.make_folds(days, 5, mode, 2.0, 7)
    assert len(folds) == 5
    index = {d: i for i, d in enumerate(days)}
    for fold in folds:
        assert index[fold.test.start] - index[fold.train.end] - 1 == 7  # exactly the embargo between
        assert fold.train.end < fold.test.start
    for earlier, later in zip(folds, folds[1:]):
        assert index[later.test.start] == index[earlier.test.end] + 1  # consecutive, never overlapping
    assert folds[-1].test.end == days[-1]  # the newest data is used
    assert len({f.test.days for f in folds}) == 1


def test_rolling_trains_on_a_fixed_length_and_anchored_from_the_start():
    days = trading_days_from(date(2022, 1, 3), 700)
    rolling = v.make_folds(days, 4, "rolling", 2.0, 5)
    anchored = v.make_folds(days, 4, "anchored", 2.0, 5)
    assert len({f.train.days for f in rolling}) == 1
    assert len({f.train.start for f in anchored}) == 1
    assert anchored[-1].train.days > anchored[0].train.days
    assert anchored[0].train.days == rolling[0].train.days  # the first fold is the same either way
    assert rolling[-1].train.start > rolling[0].train.start


def test_too_short_a_period_is_refused_with_advice():
    with pytest.raises(BacktestInputError, match="too short"):
        v.make_folds(trading_days_from(date(2024, 1, 2), 100), 6, "rolling", 2.0, 5)


# ---------------------------------------------------------------- the request


def body(**extra):
    return {"symbols": ["AAA"], "start": "2022-01-03", "end": "2024-12-31", **extra}


def test_grid_values_are_checked_and_cleaned():
    params = v.ValidationParams(**body(grid={"min_confidence_for_trade": [30, 20, 30], "slippage_bps": []}))
    assert params.grid == {"min_confidence_for_trade": [30, 20]}
    with pytest.raises(ValidationError, match="cannot be swept"):
        v.ValidationParams(**body(grid={"paper_starting_cash": [1000]}))
    with pytest.raises(ValidationError, match="between"):
        v.ValidationParams(**body(grid={"min_confidence_for_trade": [150]}))
    with pytest.raises(ValidationError, match="whole numbers"):
        v.ValidationParams(**body(grid={"max_holding_days": [10.5]}))
    with pytest.raises(ValidationError, match="at most"):
        v.ValidationParams(**body(grid={"min_confidence_for_trade": [10, 20, 30, 40, 50, 60]}))


def test_the_grid_and_run_count_are_capped():
    wide = {"min_confidence_for_trade": [10, 20, 30, 40, 50], "default_risk_pct": [0.5, 1, 1.5, 2, 2.5]}
    with pytest.raises(ValidationError, match="25 variants"):
        v.ValidationParams(**body(grid=wide))
    with pytest.raises(ValidationError, match="folds"):
        v.ValidationParams(**body(folds=1))
    with pytest.raises(ValidationError, match="runs"):
        v.ValidationParams(**body(folds=12, grid={"min_confidence_for_trade": [10, 20, 30, 40], "slippage_bps": [1, 2, 3, 4]}))


def test_variants_are_the_grid_product_in_a_fixed_order():
    grid = {"slippage_bps": [1, 5], "min_confidence_for_trade": [20, 40, 60]}
    variants = v.make_variants(grid)
    assert len(variants) == 6 == v.count_variants(grid)
    assert variants[0] == {"min_confidence_for_trade": 20, "slippage_bps": 1}
    assert variants[-1] == {"min_confidence_for_trade": 60, "slippage_bps": 5}
    assert v.make_variants({}) == [{}]


def test_n_trials_is_variants_times_folds_and_one_without_a_search():
    assert v.count_trials(6, 4) == 24
    assert v.count_trials(1, 4) == 1


# ---------------------------------------------------------------- a fake runner


class FakeRunner:
    """Window runs whose daily return depends on the confidence bar and on whether the window is
    a test window, so the in-sample best and the out-of-sample best are different variants."""

    IN_SAMPLE = {20: 0.0006, 40: 0.0020, 60: 0.0}
    OUT_OF_SAMPLE = {20: 0.0015, 40: -0.0004, 60: 0.0}

    def __init__(self, test_starts: set[date], noise: float = 0.004):
        self.test_starts = test_starts
        self.noise = noise
        self.calls: list[tuple[date, date, int, bool]] = []

    def __call__(self, params, settings, book, progress=None, should_cancel=None):
        bar = settings.min_confidence_for_trade
        is_test = params.start in self.test_starts
        self.calls.append((params.start, params.end, bar, is_test))
        days = trading_days(params.start, params.end)
        mu = (self.OUT_OF_SAMPLE if is_test else self.IN_SAMPLE).get(bar, 0.0)
        rng = np.random.default_rng(params.start.toordinal() * 7 + 11)  # the same noise for every variant of a window
        value, equity = settings.paper_starting_cash, []
        for day in days:
            value *= 1 + mu + rng.normal(0, self.noise)
            equity.append({"day": day, "equity": value, "cash": value, "open_positions": 0})
        trades = [
            {"symbol": "AAA", "direction": "long", "status": "closed", "entry_date": days[0], "exit_date": days[-1],
             "realized_pnl": 10.0 * (1 if mu > 0 else -1), "realized_r": 1.0 if mu > 0 else -1.0, "holding_days": 5,
             "close_reason": "tp1", "fees_paid": 0.0}
        ]
        return BacktestResult(status="done", trades=trades, equity=equity)


PERIOD_DAYS = trading_days_from(date(2022, 1, 3), 700)
GRID = {"min_confidence_for_trade": [20, 40, 60]}


def fake_params(folds=4, mode="rolling", grid=None):
    return v.ValidationParams(
        symbols=["AAA"], start=PERIOD_DAYS[0], end=PERIOD_DAYS[-1], folds=folds, mode=mode,
        train_ratio=2.0, embargo_days=5, grid=GRID if grid is None else grid,
    )


def run_fake(grid=None, folds=4, mode="rolling", **kw):
    params = fake_params(folds, mode, grid)
    layout = v.make_folds(trading_days(params.start, params.end), folds, mode, 2.0, 5)
    runner = FakeRunner({f.test.start for f in layout}, **kw)
    record = v.run_validation(params, AppSettings(), None, run_window=runner)
    return record, runner, layout


def test_the_in_sample_best_is_selected_and_only_it_is_tested_out_of_sample():
    record, runner, layout = run_fake()
    assert record["status"] == "done" and len(record["folds"]) == 4
    for fold, window in zip(record["folds"], layout):
        selected = fold["selected"]
        sharpes = [t["is"]["sharpe"] for t in fold["trials"]]
        assert selected["variant_index"] == sharpes.index(max(sharpes))
        assert selected["params"] == {"min_confidence_for_trade": 40}
        assert selected["in_sample"] == fold["trials"][selected["variant_index"]]["is"]
        tests = [c for c in runner.calls if c[3] and c[0] == window.test.start]
        assert len(tests) == 1 and tests[0][2] == 40  # one out-of-sample run, with the selected setting
        assert tests[0][1] == window.test.end
    # The out-of-sample result is that of the selected variant, not the out-of-sample best (20).
    assert record["aggregate"]["out_of_sample_sharpe_mean"] < record["aggregate"]["in_sample_sharpe_mean"]


def test_selection_only_uses_train_windows_that_end_before_the_test_window():
    _, runner, layout = run_fake()
    for fold in layout:
        train_calls = [c for c in runner.calls if not c[3] and c[0] == fold.train.start and c[1] == fold.train.end]
        assert len(train_calls) == 3  # every variant, on exactly the train window
        assert all(c[1] < fold.test.start for c in train_calls)


def test_trials_and_runs_are_counted():
    record, runner, _ = run_fake()
    assert record["n_variants"] == 3 and record["n_trials"] == 12 == record["dsr"]["n_trials"]
    assert len(runner.calls) == record["runs_total"] == 3 * 4 + 4
    single, runner1, _ = run_fake(grid={})
    assert single["n_trials"] == 1 and len(runner1.calls) == 4 + 4
    assert single["folds"][0]["selected"]["basis"].startswith("none")


def test_the_result_is_deterministic():
    first, _, _ = run_fake()
    second, _, _ = run_fake()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_stitched_out_of_sample_curve_is_continuous_and_covers_only_test_days():
    record, _, layout = run_fake()
    curve = record["oos_equity"]
    assert len(curve) == sum(f.test.days for f in layout)
    assert curve[0]["day"] == layout[0].test.start.isoformat() and curve[-1]["day"] == layout[-1].test.end.isoformat()
    assert [p["day"] for p in curve] == sorted(p["day"] for p in curve)
    first = record["folds"][0]["selected"]["out_of_sample"]["total_return_pct"]
    n = layout[0].test.days
    assert curve[n - 1]["equity"] / 100_000.0 - 1 == pytest.approx(first / 100, rel=1e-6)
    assert record["aggregate"]["trading_days"] == len(curve)
    assert record["aggregate"]["trade_count"] == 4


def test_the_deflated_sharpe_penalises_the_search():
    searched, _, _ = run_fake()
    single, _, _ = run_fake(grid={"min_confidence_for_trade": [40]})
    assert searched["dsr"]["available"] and searched["dsr"]["expected_max_sharpe_annualised"] > 0
    assert single["dsr"]["expected_max_sharpe_annualised"] == 0.0
    assert any("luck alone" in line for line in searched["dsr"]["reading"])
    assert 0.0 <= searched["dsr"]["dsr"] <= 1.0


def test_build_dsr_separates_skill_from_noise():
    rng = np.random.default_rng(3)
    noise = rng.normal(0, 0.01, 500)
    skill = rng.normal(0.003, 0.01, 500)
    trials = list(rng.normal(0.0, 0.05, 40))
    assert v.build_dsr(skill, 1, 1, [])["dsr"] > 0.99
    assert v.build_dsr(noise, 40, 10, trials)["dsr"] < 0.95
    assert v.build_dsr(skill, 40, 10, trials)["dsr"] < v.build_dsr(skill, 1, 1, [])["dsr"]


def test_build_dsr_says_when_it_cannot_judge():
    assert v.build_dsr(np.array([0.01, -0.01] * 10), 1, 1, [])["available"] is False
    flat = v.build_dsr(np.zeros(200), 1, 1, [])
    assert flat["available"] is False and "too few or too flat" in flat["reason"]
    unknown_spread = v.build_dsr(np.random.default_rng(1).normal(0.002, 0.01, 300), 10, 5, [0.1])
    assert unknown_spread["trial_spread_known"] is False


def test_cancel_stops_the_validation_and_keeps_the_finished_folds():
    params = fake_params(grid=GRID)
    layout = v.make_folds(trading_days(params.start, params.end), 4, "rolling", 2.0, 5)
    runner = FakeRunner({f.test.start for f in layout})
    cancel_after = (3 + 1) * 2  # two whole folds
    record = v.run_validation(
        params, AppSettings(), None, run_window=runner, should_cancel=lambda: len(runner.calls) >= cancel_after
    )
    assert record["status"] == "cancelled" and len(record["folds"]) == 2
    assert record["aggregate"] is None and record["dsr"] is None and record["oos_equity"] == []


def test_a_cancelled_window_result_also_stops_it():
    def cancelled(params, settings, book, progress=None, should_cancel=None):
        return BacktestResult(status="cancelled")

    assert v.run_validation(fake_params(), AppSettings(), None, run_window=cancelled)["status"] == "cancelled"


def test_progress_counts_runs():
    seen = []
    params = fake_params(folds=2)
    layout = v.make_folds(trading_days(params.start, params.end), 2, "rolling", 2.0, 5)
    v.run_validation(
        params, AppSettings(), None, run_window=FakeRunner({f.test.start for f in layout}),
        progress=lambda done, total, label, d, t: seen.append((done, total)),
    )
    assert seen[-1] == (8, 8) and all(total == 8 for _, total in seen)


def test_the_scorecard_lines():
    record, _, _ = run_fake()
    card = v.build_validation_scorecard(record)
    assert [c["key"] for c in card["checks"]] == ["oos_folds", "dsr"]
    folds_check, dsr_check = card["checks"]
    positive = record["aggregate"]["folds_positive"]
    assert folds_check["actual"] == f"{positive} of 4 folds positive"
    assert folds_check["status"] == ("pass" if positive * 2 > 4 else "fail")
    assert dsr_check["status"] in ("pass", "fail") and "12 tries" in dsr_check["actual"]
    assert {b["key"] for b in card["banners"]} >= {"survivorship", "price_only", "deflation", "sample_size"}
    few = v.build_validation_scorecard({**record, "aggregate": {**record["aggregate"], "folds": 2, "folds_positive": 2}})
    assert few["checks"][0]["status"] == "insufficient_data"
    unfinished = v.build_validation_scorecard({"n_trials": 1, "aggregate": None, "dsr": None})
    assert unfinished["checks"][1]["status"] == "insufficient_data"


# ---------------------------------------------------------------- the real runner: no look-ahead


def real_book(change_after: date | None = None):
    closes = wiggly_uptrend(330, daily=0.01, dip=-0.01)
    if change_after is not None:
        closes = closes.copy()
        mask = np.array([d > change_after for d in DAYS])
        closes[mask] = closes[mask] * np.linspace(1.0, 0.2, int(mask.sum()))  # a crash after the cut
    return standard_book(DAYS, {"AAA": closes})


def test_a_fold_does_not_change_when_only_later_prices_change():
    params = v.ValidationParams(
        symbols=["AAA"], start=DAYS[252], end=DAYS[329], folds=2, mode="anchored", train_ratio=1.0, embargo_days=2,
        grid={"min_confidence_for_trade": [20, 30]},
    )
    layout = v.make_folds(trading_days(params.start, params.end), 2, "anchored", 1.0, 2)
    plain = v.run_validation(params, AppSettings(), real_book())
    altered = v.run_validation(params, AppSettings(), real_book(change_after=layout[0].test.end))
    assert plain["status"] == altered["status"] == "done"
    assert json.dumps(plain["folds"][0], sort_keys=True) == json.dumps(altered["folds"][0], sort_keys=True)
    # the later fold sees the crash, so the comparison is not vacuous
    assert plain["folds"][1]["selected"]["out_of_sample"] != altered["folds"][1]["selected"]["out_of_sample"]
    assert plain["folds"][0]["selected"]["out_of_sample"]["trading_days"] == layout[0].test.days


# ---------------------------------------------------------------- jobs and endpoints


@pytest.fixture()
def env(tmp_path):
    from app.knowledge import models as _k  # noqa: F401
    from app.portfolio import models as _p  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    store = HistoryStore(tmp_path / "history.db")
    now = datetime(2025, 6, 1, tzinfo=timezone.utc)
    for symbol, series in real_book().series.items():
        store._upsert(symbol, series.frame, "yfinance", now, covered_from=DAYS[0])
    manager = BacktestManager(lambda: Session(engine), lambda: store)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[backtests_router.get_manager] = lambda: manager
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_app_settings] = lambda: AppSettings()
    yield TestClient(app), manager, engine
    manager.wait(60)
    app.dependency_overrides.clear()
    store.close()


REQUEST = {
    "symbols": ["AAA"], "start": DAYS[252].isoformat(), "end": DAYS[329].isoformat(), "folds": 2, "train_ratio": 1.0,
    "embargo_days": 2, "mode": "anchored", "grid": {"min_confidence_for_trade": [20, 30]},
}


def test_a_validation_job_runs_end_to_end(env):
    client, manager, engine = env
    response = client.post("/api/backtests/validate", json=REQUEST)
    assert response.status_code == 202, response.text
    vid = response.json()["id"]
    manager.wait(120)

    detail = client.get(f"/api/backtests/validations/{vid}").json()
    assert detail["status"] == "done" and detail["error"] is None
    assert detail["progress"]["runs_done"] == detail["progress"]["runs_total"] == 2 * 2 + 2
    result = detail["result"]
    assert len(result["folds"]) == 2 and result["n_trials"] == 4 and result["oos_equity"]
    assert [c["key"] for c in detail["scorecard"]["checks"]] == ["oos_folds", "dsr"]
    assert detail["params"]["folds"] == 2 and detail["strategy_fingerprint"]
    assert client.get("/api/backtests/validations").json()[0]["id"] == vid
    # a validation writes no BacktestRun rows
    assert client.get("/api/backtests").json() == []


def test_the_get_routes_do_not_swallow_each_other(env):
    client, _, _ = env
    assert client.get("/api/backtests/validations").json() == []
    assert client.get("/api/backtests/validations/999").status_code == 404
    options = client.get("/api/backtests/validation-options").json()
    names = {k["name"]: k for k in options["knobs"]}
    assert set(names) == set(v.KNOBS)
    assert names["min_confidence_for_trade"]["live_value"] == AppSettings().min_confidence_for_trade
    assert options["max_variants"] == v.MAX_VARIANTS and "rolling" in options["modes"]


def test_bad_requests_are_refused(env):
    client, _, _ = env
    assert client.post("/api/backtests/validate", json={**REQUEST, "grid": {"bogus": [1]}}).status_code == 422
    short = {**REQUEST, "start": DAYS[290].isoformat(), "folds": 6}
    response = client.post("/api/backtests/validate", json=short)
    assert response.status_code == 400 and "too short" in response.json()["detail"]
    late = {**REQUEST, "end": "2030-01-01"}
    assert client.post("/api/backtests/validate", json=late).status_code == 400


def test_one_job_at_a_time(env):
    client, manager, engine = env
    with Session(engine) as session:
        session.add(BacktestValidation(status="running"))
        session.commit()
    assert client.post("/api/backtests/validate", json=REQUEST).status_code == 409


def test_cancelling_a_running_validation(env, monkeypatch):
    client, manager, engine = env
    started = threading.Event()

    def blocking(params, base, book, *, progress=None, should_cancel=None, on_fold_done=None):
        started.set()
        while not should_cancel():
            time.sleep(0.01)
        return {"status": "cancelled", "folds": [], "aggregate": None, "dsr": None, "oos_equity": []}

    monkeypatch.setattr(service, "run_validation", blocking)
    vid = client.post("/api/backtests/validate", json=REQUEST).json()["id"]
    assert started.wait(10)
    assert client.post(f"/api/backtests/validations/{vid}/cancel").json()["status"] == "cancelling"
    manager.wait(10)
    detail = client.get(f"/api/backtests/validations/{vid}").json()
    assert detail["status"] == "cancelled" and detail["scorecard"] is None
    assert client.post(f"/api/backtests/validations/{vid}/cancel").json()["status"] == "cancelled"
    assert client.post("/api/backtests/validations/999/cancel").status_code == 404


def test_a_failure_ends_in_a_final_state(env, monkeypatch):
    client, manager, _ = env

    def boom(*args, **kwargs):
        raise RuntimeError("kaput")

    monkeypatch.setattr(service, "run_validation", boom)
    vid = client.post("/api/backtests/validate", json=REQUEST).json()["id"]
    manager.wait(10)
    detail = client.get(f"/api/backtests/validations/{vid}").json()
    assert detail["status"] == "failed" and "kaput" in detail["error"]


def test_a_validation_left_running_is_marked_failed_at_startup(env):
    _, _, engine = env
    with Session(engine) as session:
        session.add(BacktestValidation(status="running"))
        session.commit()
    assert service.recover_interrupted_runs(lambda: Session(engine)) == 1
    with Session(engine) as session:
        row = session.get(BacktestValidation, 1)
        assert row.status == "failed" and "interrupted" in row.error
