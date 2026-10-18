from fastapi.testclient import TestClient

from app.analysis.quality_gate import GateCheck, GateResult
from app.main import app


def test_rate_one_saves_and_stays_out_of_runs(monkeypatch):
    gate = GateResult(True, [GateCheck("rating", True, "note")], dip=False,
                      detail={"points": 7, "max": 32, "percent": 22, "bar_pct": 16, "parts": {"technical": 7}, "why": {}})
    monkeypatch.setattr("app.api.routers.ratings.rate_stock", lambda *a, **k: gate)
    c = TestClient(app)
    r = c.post("/api/ratings/stock/zzrate")
    assert r.status_code == 200 and r.json()["symbol"] == "ZZRATE" and r.json()["points"] == 7
    assert c.get("/api/ratings/latest").json()["ZZRATE"]["percent"] == 22
    assert c.get("/api/ratings/stock/ZZRATE").json()[0]["detail"]["parts"] == {"technical": 7}
    assert all(run["source"] != "single" for run in c.get("/api/ratings/runs").json())
