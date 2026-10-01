"""GET /api/cache/status and POST /api/cache/clear (the Settings page's data-cache card)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.data_providers import cache as cache_module
from app.data_providers.cache import cached, clear_cache, configure_persistence, reset_cache_counters
from app.data_providers.cache_store import PersistentCacheStore
from app.data_providers.history_store import HistoryStore, configure_history_store
from app.main import app

client = TestClient(app)


class _Bars:
    name = "yfinance"

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        days = pd.bdate_range("2026-09-01", "2026-09-30")
        return pd.DataFrame(
            {"date": days, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0}
        )


class _Quoter:
    name = "fake"

    @cached(60)
    def quote(self, symbol):
        return {"symbol": symbol}


@pytest.fixture
def stores(tmp_path):
    cache_store = PersistentCacheStore(tmp_path / "cache.db", write_behind=False)
    configure_persistence(cache_store)
    history = HistoryStore(tmp_path / "history.db", _Bars(), clock=lambda: datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc))
    configure_history_store(history)
    clear_cache()
    reset_cache_counters()
    yield cache_store, history
    configure_persistence(None)
    configure_history_store(None)
    clear_cache()
    reset_cache_counters()


def test_status_reports_both_layers_and_the_history_store(stores):
    cache_store, history = stores
    q = _Quoter()
    q.quote("AAPL")
    q.quote("AAPL")
    cache_store.flush()
    history.get_daily_history("AAPL", start=date(2026, 9, 1))

    body = client.get("/api/cache/status").json()
    assert body["memory_entries"] == 1
    assert body["counters"]["misses"] == 1 and body["counters"]["memory_hits"] == 1
    assert body["hit_rate"] == 0.5
    persistent = body["persistent"]
    assert persistent["entries"] == 1 and persistent["fresh_entries"] == 1
    assert persistent["file_name"] == "cache.db"
    assert persistent["payload_bytes"] > 0 and persistent["newest_stored_at"].endswith("Z")
    assert "/" not in persistent["file_name"] and "\\" not in persistent["file_name"]  # no server paths leak
    hist = body["history"]
    assert hist["symbols"] == 1 and hist["bars"] == 22
    assert hist["first_date"] == "2026-09-01" and hist["last_date"] == "2026-09-30"
    assert hist["stalest"] == [{"symbol": "AAPL", "last_date": "2026-09-30"}]


def test_status_with_persistence_off_says_so(tmp_path):
    configure_persistence(None)
    configure_history_store(HistoryStore(tmp_path / "h.db", _Bars()))
    try:
        body = client.get("/api/cache/status").json()
        assert body["persistent"] is None
        assert body["history"] == {"symbols": 0, "bars": 0, "first_date": None, "last_date": None, "file_bytes": body["history"]["file_bytes"], "stalest": []}
        assert body["hit_rate"] is None
    finally:
        configure_history_store(None)


def test_status_is_read_only(tmp_path):
    """Asking must not flush queued writes or touch the history file."""
    store = PersistentCacheStore(tmp_path / "cache.db", write_behind=True, batch_delay_seconds=60)
    configure_persistence(store)
    history = HistoryStore(tmp_path / "history.db", _Bars())
    configure_history_store(history)
    try:
        clear_cache()
        _Quoter().quote("AAPL")  # queued, not committed
        assert store.stats().pending_writes == 1
        for _ in range(2):
            client.get("/api/cache/status")
        assert store.stats().pending_writes == 1 and store.stats().entries == 0
        assert history.summary().bars == 0
    finally:
        configure_persistence(None)
        configure_history_store(None)
        clear_cache()


def test_status_does_not_create_a_history_file(tmp_path, monkeypatch):
    configure_history_store(None)
    configure_persistence(None)
    monkeypatch.setenv("SETTINGS_PATH", str(tmp_path / "rt" / "settings.json"))
    body = client.get("/api/cache/status").json()
    assert body["history"]["symbols"] == 0
    assert not (tmp_path / "rt").exists()


def test_clear_empties_the_provider_cache_but_not_the_history_store(stores):
    cache_store, history = stores
    _Quoter().quote("AAPL")
    history.get_daily_history("AAPL", start=date(2026, 9, 1))
    assert cache_store.stats().entries == 1

    resp = client.post("/api/cache/clear")
    assert resp.status_code == 200 and resp.json()["cleared"] is True
    assert cache_store.stats().entries == 0
    assert cache_module.cache_stats().memory_entries == 0
    assert history.summary().bars == 22, "the history store is data you chose to keep"


def test_clear_has_a_cooldown(stores):
    assert client.post("/api/cache/clear").status_code == 200
    second = client.post("/api/cache/clear")
    assert second.status_code == 429
    assert "wait" in second.json()["detail"]


def test_cooldown_resets_between_tests(stores):
    """conftest.py resets the clear cooldown, or this would 429 after the test above."""
    assert client.post("/api/cache/clear").status_code == 200
