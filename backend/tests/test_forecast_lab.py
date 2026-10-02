"""Forecast Lab: features, the walk-forward split, the stop-only opinion and the API.
Synthetic trades only, in-memory database, no network. Tests that fit a model skip
when lightgbm / shap are not installed."""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_app_settings, get_session
from app.backtest.models import RUN_DONE, BacktestRun, BacktestTrade
from app.config import AppSettings
from app.main import app
from app.ml import runtime, stats
from app.ml.dataset import Row, load_rows, walk_forward_split
from app.ml.features import FEATURE_NAMES, feature_vector
from app.ml.models import MlModel, MlPrediction
from app.ml.service import (
    MIN_ROWS,
    MlTrainingError,
    ml_opinion_for_plan,
    record_ml_opinion,
    set_active,
    train_model,
)
from app.portfolio.sleeves import create_sleeve, ensure_core_sleeve

D0 = date(2025, 1, 1)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(db):
    with Session(db) as s:
        yield s


def make_run(session, n=240, seed=3, hold_days=3) -> int:
    """A finished run of n closed trades, one entry per day; R rises with technical_score."""
    rng = random.Random(seed)
    run = BacktestRun(status=RUN_DONE, finished_at=datetime(2026, 1, 1))
    session.add(run)
    session.commit()
    session.refresh(run)
    for i in range(n):
        tech = rng.choice([0, 1, 2, 3, 4])
        scores = {"technical_score": tech, "market_confirmation_score": rng.choice([-1, 0, 1]), "vix_regime_score": 0}
        r = 0.6 * (tech - 2) + rng.gauss(0, 0.5)
        entry = D0 + timedelta(days=i)
        session.add(
            BacktestTrade(
                run_id=run.id, symbol="AAA", direction="long", status="closed",
                entry_at=datetime.combine(entry, datetime.min.time()), entry_date=entry,
                entry_price=100, stop_loss=95, tp1=110, shares=10,
                exit_date=entry + timedelta(days=hold_days), realized_r=r,
                confidence_points=5 + tech, scores_json=json.dumps(scores),
            )
        )
    session.commit()
    return run.id


# ------------------------------------------------------------------ pure parts


def test_feature_vector_order_and_missing_values():
    row = feature_vector({"technical_score": 3, "news_score": None}, 7, "long")
    assert len(row) == len(FEATURE_NAMES)
    assert row[FEATURE_NAMES.index("technical_score")] == 3.0
    assert row[FEATURE_NAMES.index("news_score")] == 0.0
    assert row[FEATURE_NAMES.index("confidence_points")] == 7.0
    assert row[FEATURE_NAMES.index("is_long")] == 1.0
    assert feature_vector({}, None, "short")[-1] == 0.0


def test_split_is_chronological_and_purges_labels_not_yet_known():
    rows = [Row(D0 + timedelta(days=i), D0 + timedelta(days=i + 5), "A", [0.0], 0.1) for i in range(100)]
    split = walk_forward_split(rows)
    assert split is not None
    assert max(r.entry_date for r in split.train) < split.validation_start <= min(r.entry_date for r in split.validation)
    assert max(r.entry_date for r in split.validation) < split.test_start <= min(r.entry_date for r in split.test)
    # no label in an earlier window resolves after the next window begins
    assert all(r.exit_date < split.validation_start for r in split.train)
    assert all(r.exit_date < split.test_start for r in split.validation)
    assert split.purged > 0


def test_split_refuses_too_little_data():
    assert walk_forward_split([]) is None
    assert walk_forward_split([Row(D0, D0, "A", [0.0], 0.0)] * 5) is None  # one distinct day


def test_load_rows_skips_open_and_unscored_trades(session):
    run_id = make_run(session, n=10)
    session.add(
        BacktestTrade(
            run_id=run_id, symbol="B", direction="long", status="open", entry_at=datetime(2025, 6, 1),
            entry_date=D0, entry_price=1, stop_loss=1, tp1=2, shares=1, scores_json="{}",
        )
    )
    session.commit()
    assert len(load_rows(session, [run_id])) == 10
    assert load_rows(session, []) == []


def test_verdict_needs_enough_trades_and_a_clear_interval():
    assert stats.verdict(5, {"ic": 0.9, "low": 0.5, "high": 1.0}) == "not_enough_data"
    assert stats.verdict(50, {"ic": 0.3, "low": 0.1, "high": 0.5}) == "edge_out_of_sample"
    assert stats.verdict(50, {"ic": 0.1, "low": -0.1, "high": 0.3}) == "no_clear_edge"
    assert stats.verdict(50, {"ic": -0.3, "low": -0.5, "high": -0.1}) == "inverted"


def test_extras_status_never_raises_and_names_what_is_missing(monkeypatch):
    monkeypatch.setattr(runtime.importlib.util, "find_spec", lambda name: None)
    status = runtime.extras_status()
    assert not status.available and "lightgbm" in status.missing and "not installed" in status.message
    with pytest.raises(runtime.MlExtrasMissing):
        runtime.require_extras()


# ------------------------------------------------------------ opinion gating


def test_no_opinion_unless_enabled_and_an_ml_sleeve(session):
    core = ensure_core_sleeve(session)
    ml = create_sleeve(session, "ML Forecast", "ml", 1000)
    kwargs = dict(scores={}, confidence_points=6, direction="long")
    on = AppSettings(ml_style_enabled=True)
    assert ml_opinion_for_plan(session, AppSettings(), ml, **kwargs) is None  # off by default
    assert ml_opinion_for_plan(session, on, core, **kwargs) is None  # core is never asked
    assert ml_opinion_for_plan(session, on, None, **kwargs) is None
    other = create_sleeve(session, "Momentum", "momentum", 1000)
    assert ml_opinion_for_plan(session, on, other, **kwargs) is None


def test_without_a_model_the_opinion_is_unavailable_and_stops_nothing(session):
    ml = create_sleeve(session, "ML Forecast", "ml", 1000)
    op = ml_opinion_for_plan(session, AppSettings(ml_style_enabled=True), ml, scores={}, confidence_points=6, direction="long")
    assert op is not None and not op.available and not op.stops_trade
    record_ml_opinion(session, 11, op)
    row = session.exec(select(MlPrediction)).one()
    assert row.plan_id == 11 and row.available is False and row.stopped_trade is False


def test_training_below_the_minimum_is_refused_with_a_message(session):
    pytest.importorskip("lightgbm")
    pytest.importorskip("shap")
    run_id = make_run(session, n=MIN_ROWS - 1)
    with pytest.raises(MlTrainingError, match="at least"):
        train_model(session, [run_id])


# ------------------------------------------------------------- fitted model


def fit(session, **kw):
    pytest.importorskip("lightgbm")
    pytest.importorskip("shap")
    pytest.importorskip("sklearn")
    run_id = make_run(session, **kw)
    return run_id, train_model(session, [run_id])


def test_training_stores_metrics_with_n_and_intervals_and_is_not_active(session):
    _, model = fit(session)
    metrics = json.loads(model.metrics_json)
    split = json.loads(model.split_json)
    assert model.active is False
    assert split["n_train"] + split["n_validation"] + split["n_test"] + split["purged_rows"] == split["n_rows"]
    assert metrics["ic"]["n"] == split["n_test"]
    assert metrics["ic"]["ic"] > 0.3  # the synthetic signal is real
    assert metrics["ic"]["low"] is not None and metrics["ic"]["high"] is not None
    assert metrics["kept_by_model"]["n"] + metrics["stopped_by_model"]["n"] == split["n_test"]
    importance = json.loads(model.importance_json)
    assert importance[0]["feature"] == "technical_score"  # the driver we built in


def test_test_window_never_influences_the_fitted_model(session, db):
    """Rewriting the test window's outcomes must leave the model byte-identical."""
    run_id, first = fit(session)
    split = json.loads(first.split_json)
    test_start = date.fromisoformat(split["test_start"])
    for t in session.exec(select(BacktestTrade).where(BacktestTrade.entry_date >= test_start)).all():
        t.realized_r = -5.0 * (t.realized_r or 1)
        session.add(t)
    session.commit()
    second = train_model(session, [run_id])
    assert second.model_text == first.model_text
    assert json.loads(second.metrics_json)["ic"]["ic"] != json.loads(first.metrics_json)["ic"]["ic"]


def test_active_model_can_only_stop_and_explains_with_shap(session):
    _, model = fit(session)
    set_active(session, model.id)
    ml = create_sleeve(session, "ML Forecast", "ml", 1000)
    settings = AppSettings(ml_style_enabled=True, ml_min_expected_r=0.0)
    weak = ml_opinion_for_plan(
        session, settings, ml, scores={"technical_score": 0}, confidence_points=5, direction="long"
    )
    strong = ml_opinion_for_plan(
        session, settings, ml, scores={"technical_score": 4}, confidence_points=9, direction="long"
    )
    assert weak.available and weak.stops_trade and weak.expected_r < 0
    assert strong.available and not strong.stops_trade and strong.expected_r > 0
    assert strong.top_features[0]["feature"] == "technical_score"
    assert {"feature", "value", "shap"} <= set(strong.top_features[0])
    # the opinion carries no direction, entry, stop or size to apply
    assert not any(hasattr(strong, a) for a in ("direction", "entry", "stop", "shares", "size"))


def test_only_one_model_is_active(session):
    _, a = fit(session)
    b = train_model(session, [a.id and json.loads(a.run_ids_json)[0]])
    set_active(session, a.id)
    set_active(session, b.id)
    assert [m.id for m in session.exec(select(MlModel).where(MlModel.active == True)).all()] == [b.id]  # noqa: E712
    set_active(session, None)
    assert session.exec(select(MlModel).where(MlModel.active == True)).first() is None  # noqa: E712


# --------------------------------------------------------------------- API


@pytest.fixture
def client(db):
    def _session():
        with Session(db) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_app_settings] = lambda: AppSettings()
    yield TestClient(app)
    for dep in (get_session, get_app_settings):
        app.dependency_overrides.pop(dep, None)


def test_status_reports_off_by_default_and_lists_runs(client, session):
    run_id = make_run(session, n=30)
    body = client.get("/api/forecast-lab/status").json()
    assert body["enabled"] is False and body["active_model"] is None
    assert body["runs"] == [{"id": run_id, "finished_at": body["runs"][0]["finished_at"], "closed_trades": 30}]
    assert isinstance(body["extras_available"], bool) and body["extras_message"]


def test_train_without_extras_says_so_clearly(client, session, monkeypatch):
    run_id = make_run(session, n=30)
    monkeypatch.setattr(runtime.importlib.util, "find_spec", lambda name: None)
    res = client.post("/api/forecast-lab/models/train", json={"run_ids": [run_id]})
    assert res.status_code == 409 and "ML extras not installed" in res.json()["detail"]


def test_train_rejects_unfinished_runs(client, session):
    res = client.post("/api/forecast-lab/models/train", json={"run_ids": [999]})
    assert res.status_code == 400


def test_ml_sleeve_is_created_once_and_train_activate_roundtrip(client, session):
    assert client.post("/api/forecast-lab/sleeve").status_code == 201
    assert client.post("/api/forecast-lab/sleeve").status_code == 409
    assert client.get("/api/forecast-lab/status").json()["ml_sleeve_key"] == "ml-forecast"
    pytest.importorskip("lightgbm")
    pytest.importorskip("shap")
    pytest.importorskip("sklearn")
    run_id = make_run(session)
    trained = client.post("/api/forecast-lab/models/train", json={"run_ids": [run_id]})
    assert trained.status_code == 201
    mid = trained.json()["id"]
    assert trained.json()["active"] is False and trained.json()["importance"]
    assert client.post(f"/api/forecast-lab/models/{mid}/activate").json()["active"] is True
    assert client.get("/api/forecast-lab/status").json()["active_model"]["id"] == mid
    assert client.post("/api/forecast-lab/models/deactivate").status_code == 200
    assert client.get("/api/forecast-lab/predictions").json() == []
    assert client.delete(f"/api/forecast-lab/models/{mid}").status_code == 200
