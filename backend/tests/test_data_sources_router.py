"""GET /api/data-sources and POST /api/data-sources/{name}/probe. No network."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.data_providers import health
from app.main import app
from app.services import data_sources_service as service

client = TestClient(app)


def test_empty_state_lists_the_chain_in_order_with_no_numbers():
    body = client.get("/api/data-sources").json()
    names = [row["name"] for row in body["chain"]]
    assert "yfinance" in names and "nasdaq" in names and "stooq" in names
    assert names.index("yfinance") < names.index("nasdaq") < names.index("stooq")
    assert [row["position"] for row in body["chain"]] == list(range(1, len(names) + 1))
    assert body["window_size"] == health.WINDOW_SIZE
    for row in body["chain"] + body["others"]:
        assert row["status"] == "unused"
        assert row["calls"] == 0 and row["success_rate"] is None and row["latency_p50_ms"] is None
    assert {row["name"] for row in body["others"]} == {"sec_filings", "finra", "ecb", "fred", "house_clerk"}
    assert not next(r for r in body["others"] if r["name"] == "finra")["can_probe"]


def test_recorded_calls_show_up_with_their_numbers():
    health.record_call("yfinance", "get_quote", True, 120.0)
    health.record_call("yfinance", "get_quote", True, 80.0)
    health.record_call("nasdaq", "get_quote", False, 40.0, "HTTP 503")
    health.record_call("ecb", "get_series", True, 300.0)
    body = client.get("/api/data-sources").json()
    yf = next(r for r in body["chain"] if r["name"] == "yfinance")
    assert yf["status"] == "healthy" and yf["calls"] == 2 and yf["success_rate"] == 1.0
    assert yf["latency_p50_ms"] == 100.0 and yf["last_success_at"].endswith("Z")
    nasdaq = next(r for r in body["chain"] if r["name"] == "nasdaq")
    assert nasdaq["last_error"] == "HTTP 503" and nasdaq["failures"] == 1
    ecb = next(r for r in body["others"] if r["name"] == "ecb")
    assert ecb["calls"] == 1 and ecb["can_probe"]


def test_get_never_writes_health():
    client.get("/api/data-sources")
    client.get("/api/data-sources")
    assert health.snapshot() == {}


def test_probe_reports_success_and_records_it(monkeypatch):
    monkeypatch.setitem(service._PROBE_RUNNERS, "fred", lambda settings: "10-year yield 5.17% on 2026-09-25")
    result = client.post("/api/data-sources/fred/probe")
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is True and "5.17" in body["detail"] and body["latency_ms"] >= 0
    assert health.snapshot()["fred"].successes == 1


def test_probe_failure_is_an_answer_not_an_error(monkeypatch):
    def broken(settings):
        raise RuntimeError("down https://x.example/api?key=SECRET123")

    monkeypatch.setitem(service._PROBE_RUNNERS, "ecb", broken)
    result = client.post("/api/data-sources/ecb/probe")
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is False and "SECRET123" not in body["error"]
    assert health.snapshot()["ecb"].failures == 1


def test_probe_cooldown_per_source(monkeypatch):
    monkeypatch.setitem(service._PROBE_RUNNERS, "fred", lambda settings: "ok")
    monkeypatch.setitem(service._PROBE_RUNNERS, "ecb", lambda settings: "ok")
    assert client.post("/api/data-sources/fred/probe").status_code == 200
    again = client.post("/api/data-sources/fred/probe")
    assert again.status_code == 429
    assert client.post("/api/data-sources/ecb/probe").status_code == 200  # a different source is fine


def test_probe_unknown_and_unprobeable():
    assert client.post("/api/data-sources/nonsense/probe").status_code == 404
    assert client.post("/api/data-sources/finra/probe").status_code == 400


def test_chain_probe_bypasses_the_cache(monkeypatch):
    from app.data_providers.cache import cached

    calls = []

    class Fake:
        name = "yfinance"

        @cached(60)
        def get_quote(self, symbol):
            calls.append(symbol)

            class Q:
                price = 500.0

            return Q()

    class FakeComposite:
        _providers = [Fake()]

    monkeypatch.setattr(service, "get_data_provider", lambda settings: FakeComposite())
    fake = FakeComposite._providers[0]
    fake.get_quote("SPY")  # warm the cache
    result = client.post("/api/data-sources/yfinance/probe").json()
    assert result["ok"] is True and "500.0" in result["detail"]
    assert calls == ["SPY", "SPY"]  # the probe went to the source, not the cached copy


@pytest.mark.parametrize("path", ["/api/data-sources", "/api/macro/series"])
def test_routes_need_auth_when_it_is_required(path, monkeypatch):
    # The routers carry the shared auth dependency like every other router.
    from app.api.deps import require_auth

    for route in app.routes:
        if getattr(route, "path", None) == path:
            assert any(d.call is require_auth for d in route.dependant.dependencies)
