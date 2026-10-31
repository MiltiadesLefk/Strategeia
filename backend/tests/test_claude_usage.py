import time

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.database import engine
from app.llm_providers import usage_meter
from app.main import app
from app.portfolio.models import RunUsage


def test_meter_counts_all_input_kinds_and_ignores_junk():
    before = usage_meter.snapshot()
    usage_meter.record({"usage": {"input_tokens": 2, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 10, "output_tokens": 7}})
    usage_meter.record({"no": "usage"})
    after = usage_meter.snapshot()
    assert after["calls"] - before["calls"] == 1
    assert after["tokens_in"] - before["tokens_in"] == 112 and after["tokens_out"] - before["tokens_out"] == 7


def test_session_endpoint_reports_unavailable_without_guessing(monkeypatch):
    monkeypatch.setattr("app.api.routers.usage.get_session_usage", lambda *a, **k: None)
    body = TestClient(app).get("/api/usage/session").json()
    assert body["available"] is False and "five_hour_pct" not in body


def test_session_endpoint_passes_the_windows_through(monkeypatch):
    monkeypatch.setattr("app.api.routers.usage.get_session_usage", lambda *a, **k: {"five_hour_pct": 4.0, "seven_day_pct": 1.0})
    body = TestClient(app).get("/api/usage/session").json()
    assert body["available"] and body["five_hour_pct"] == 4.0


def test_rate_all_run_stores_what_it_used(monkeypatch):
    SQLModel.metadata.create_all(engine)  # the table is created by the app's startup, which this client does not run
    windows = iter([{"five_hour_pct": 3.0}, {"five_hour_pct": 4.5}])
    monkeypatch.setattr("app.api.routers.ratings.get_session_usage", lambda *a, **k: next(windows))

    def fake_rate(*a, **k):
        usage_meter.record({"usage": {"input_tokens": 5, "output_tokens": 2}})
        return 0

    monkeypatch.setattr("app.api.routers.ratings.rate_universe", fake_rate)
    c = TestClient(app)
    run_id = c.post("/api/ratings/run").json()["run_id"]
    for _ in range(50):
        if not any(r["running"] for r in c.get("/api/ratings/runs").json()):
            break
        time.sleep(0.1)
    usage = c.get(f"/api/ratings/runs/{run_id}")
    assert usage.status_code in (200, 404)  # no rating rows were saved, so only the usage row exists
    with Session(engine) as s:
        row = s.get(RunUsage, run_id)
    assert row.calls == 1 and row.tokens_in == 5 and row.tokens_out == 2
    assert (row.five_hour_after - row.five_hour_before) == 1.5
