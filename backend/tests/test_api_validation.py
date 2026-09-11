from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi.testclient import TestClient

from app.api.deps import get_data_provider
from app.data_providers.base import AllProvidersFailedError
from app.main import app

client = TestClient(app)


def test_risk_calculate_rejects_negative_risk_pct():
    """Regression: a negative risk_pct used to silently flow through to
    negative `shares`, which flips the sign of realized P&L relative to
    actual price movement in the paper-trading engine — a stop-loss hit
    would register as a profit. Must be rejected at the API boundary."""
    resp = client.post(
        "/api/risk/calculate",
        json={"symbol": "AAPL", "account_size": 10000, "risk_pct": -100, "entry": 100, "stop": 95, "direction": "long"},
    )
    assert resp.status_code == 422


def test_risk_calculate_rejects_negative_account_size():
    resp = client.post(
        "/api/risk/calculate",
        json={"symbol": "AAPL", "account_size": -50000, "risk_pct": 1, "entry": 100, "stop": 95, "direction": "long"},
    )
    assert resp.status_code == 422


def test_risk_calculate_rejects_invalid_direction():
    resp = client.post(
        "/api/risk/calculate",
        json={"symbol": "AAPL", "account_size": 10000, "risk_pct": 1, "entry": 100, "stop": 95, "direction": "sideways"},
    )
    assert resp.status_code == 422


def test_risk_calculate_rejects_risk_pct_over_100():
    resp = client.post(
        "/api/risk/calculate",
        json={"symbol": "AAPL", "account_size": 10000, "risk_pct": 500, "entry": 100, "stop": 95, "direction": "long"},
    )
    assert resp.status_code == 422


def test_trade_plan_generate_rejects_negative_risk_pct():
    resp = client.post("/api/trade-plans/generate", json={"symbol": "AAPL", "account_size": 10000, "risk_pct": -5})
    assert resp.status_code == 422


def test_trade_plan_generate_rejects_negative_account_size():
    resp = client.post("/api/trade-plans/generate", json={"symbol": "AAPL", "account_size": -1, "risk_pct": 1})
    assert resp.status_code == 422


def test_settings_update_rejects_negative_starting_cash():
    resp = client.put("/api/settings", json={"paper_starting_cash": -100})
    assert resp.status_code == 422


def test_settings_update_rejects_negative_default_risk_pct():
    resp = client.put("/api/settings", json={"default_risk_pct": -1})
    assert resp.status_code == 422


class AlwaysFailingProvider:
    name = "failing"

    def get_ohlcv(self, symbol, period="6mo", interval="1d") -> pd.DataFrame:
        raise AllProvidersFailedError(f"no data for {symbol}")

    def get_quote(self, symbol):
        raise AllProvidersFailedError(f"no data for {symbol}")

    def get_company_overview(self, symbol):
        raise AllProvidersFailedError(f"no data for {symbol}")

    def get_financials(self, symbol):
        raise AllProvidersFailedError(f"no data for {symbol}")

    def get_news(self, symbol, limit=5):
        raise AllProvidersFailedError(f"no data for {symbol}")

    def get_earnings_date(self, symbol) -> date | None:
        return None


class _FakeInfraSettings:
    def __init__(self, api_shared_secret: str):
        self.api_shared_secret = api_shared_secret


def test_shared_secret_unset_leaves_api_open(monkeypatch):
    """Default behavior — no API_SHARED_SECRET configured — must stay a
    no-op: every endpoint open, exactly as before this stop-gap existed."""
    monkeypatch.setattr("app.api.deps.get_infra_settings", lambda: _FakeInfraSettings(""))
    resp = client.get("/api/settings")
    assert resp.status_code == 200


def test_shared_secret_rejects_missing_or_wrong_key(monkeypatch):
    monkeypatch.setattr("app.api.deps.get_infra_settings", lambda: _FakeInfraSettings("s3cr3t-value"))
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/settings", headers={"X-API-Key": "wrong"}).status_code == 401


def test_shared_secret_accepts_matching_key(monkeypatch):
    monkeypatch.setattr("app.api.deps.get_infra_settings", lambda: _FakeInfraSettings("s3cr3t-value"))
    resp = client.get("/api/settings", headers={"X-API-Key": "s3cr3t-value"})
    assert resp.status_code == 200


def test_invalid_symbol_returns_clean_502_not_bare_500():
    """Regression: hitting /api/analysis/<garbage> used to bubble
    AllProvidersFailedError all the way up as an unhandled 500 with a bare
    'Internal Server Error' body and no useful detail."""
    app.dependency_overrides[get_data_provider] = lambda: AlwaysFailingProvider()
    try:
        resp = client.get("/api/analysis/GARBAGEXYZ123NOTREAL")
        assert resp.status_code == 502
        assert "No data available" in resp.json()["detail"]
    finally:
        app.dependency_overrides.clear()
