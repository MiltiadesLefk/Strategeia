from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.routers import scanner as scanner_router
from app.config import AppSettings
from app.main import app
from app.services.automation_service import AutoScanOutcome

client = TestClient(app)


def test_scan_rejects_too_many_symbols():
    """Regression: the `symbols` query param had no length cap — one
    unauthenticated GET with thousands of comma-separated junk symbols could
    force thousands of sequential data-provider calls. This is checked
    before any provider is touched, so no fake provider/network is needed
    here."""
    symbols = ",".join(f"SYM{i}" for i in range(scanner_router.MAX_SCAN_SYMBOLS + 1))
    resp = client.get(f"/api/scan?symbols={symbols}")
    assert resp.status_code == 400
    assert "Too many symbols" in resp.json()["detail"]


def test_auto_trade_cooldown_rejects_immediate_repeat_call(monkeypatch):
    """Regression: /api/scan/auto-trade is the biggest cost/exposure
    amplifier in the API (fans out across the whole scan universe, can
    auto-execute a real paper position per qualifying symbol) and had no
    guard at all against being called in a tight loop."""
    monkeypatch.setattr(scanner_router, "_last_auto_trade_run_monotonic", None)
    monkeypatch.setattr(scanner_router, "run_auto_scan", lambda *a, **k: AutoScanOutcome())

    settings = AppSettings()
    first = scanner_router.run_auto_trade_now(data_provider=None, llm_provider=None, settings=settings, session=None)
    assert first.generated == []

    with pytest.raises(HTTPException) as exc_info:
        scanner_router.run_auto_trade_now(data_provider=None, llm_provider=None, settings=settings, session=None)
    assert exc_info.value.status_code == 429
