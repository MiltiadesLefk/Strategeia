"""The data-source health recorder: maths, windowing, simulation switch, wiring."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.data_providers import health
from app.data_providers.base import AllProvidersFailedError, DataProviderError
from app.data_providers.cache import cached, clear_cache, configure_persistence
from app.data_providers.composite_provider import CompositeDataProvider
from app.knowledge.point_in_time import as_of


@pytest.fixture(autouse=True)
def _clean():
    health.reset()
    configure_persistence(None)
    clear_cache()
    yield
    health.reset()


def test_percentile_matches_linear_interpolation():
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert health.percentile(values, 50) == 30.0
    assert health.percentile(values, 0) == 10.0
    assert health.percentile(values, 100) == 50.0
    assert health.percentile(values, 95) == pytest.approx(48.0)
    assert health.percentile([7.0], 95) == 7.0
    assert health.percentile([], 50) is None


def test_counts_latency_and_last_error():
    health.record_call("yfinance", "get_quote", True, 100.0)
    health.record_call("yfinance", "get_quote", True, 300.0)
    health.record_call("yfinance", "get_ohlcv", False, 50.0, "boom")
    snap = health.snapshot()["yfinance"]
    assert (snap.calls, snap.successes, snap.failures) == (3, 2, 1)
    assert snap.latency_p50_ms == 100.0
    assert snap.latency_samples == 3
    assert snap.last_error == "boom"
    assert snap.consecutive_failures == 1
    assert snap.last_success_at is not None
    assert {m.method for m in snap.methods} == {"get_quote", "get_ohlcv"}


def test_consecutive_failures_reset_on_success_and_set_status():
    for _ in range(3):
        health.record_call("nasdaq", "get_quote", False, 10.0, "down")
    assert health.snapshot()["nasdaq"].status == health.STATUS_FAILING
    health.record_call("nasdaq", "get_quote", True, 10.0)
    snap = health.snapshot()["nasdaq"]
    assert snap.consecutive_failures == 0
    # 1 success in 4 is still under half over the window.
    assert snap.status == health.STATUS_FAILING


def test_status_labels():
    assert health.classify(0, None, 0, 0) == health.STATUS_UNUSED
    assert health.classify(10, 1.0, 0, 0) == health.STATUS_HEALTHY
    assert health.classify(10, 1.0, 1, 0) == health.STATUS_HEALTHY  # one miss is normal
    assert health.classify(10, 0.95, 2, 0) == health.STATUS_DEGRADED
    assert health.classify(10, 0.8, 0, 0) == health.STATUS_DEGRADED
    assert health.classify(10, 1.0, 0, 1) == health.STATUS_DEGRADED  # served old data lately
    assert health.classify(10, 0.4, 0, 0) == health.STATUS_FAILING
    assert health.classify(10, 1.0, 3, 0) == health.STATUS_FAILING


def test_window_keeps_only_the_newest_calls():
    for _ in range(health.WINDOW_SIZE):
        health.record_call("stooq", "get_ohlcv", False, 5.0, "old failure")
    for _ in range(health.WINDOW_SIZE):
        health.record_call("stooq", "get_ohlcv", True, 5.0)
    snap = health.snapshot()["stooq"]
    assert snap.calls == 2 * health.WINDOW_SIZE  # lifetime counter keeps everything
    assert snap.window_calls == health.WINDOW_SIZE
    assert snap.window_success_rate == 1.0
    assert snap.status == health.STATUS_HEALTHY


def test_errors_are_scrubbed_and_truncated():
    text = health.scrub_error("GET https://api.example.com/q?symbol=SPY&token=abc123 failed")
    assert "abc123" not in text and "symbol=SPY" not in text
    assert "https://api.example.com/q?..." in text
    assert "api_key=***" in health.scrub_error("bad api_key=SECRET here")
    assert len(health.scrub_error("x" * 1000)) <= health.MAX_ERROR_CHARS


def test_nothing_is_recorded_while_a_moment_is_simulated():
    with as_of(datetime(2024, 1, 2)):
        health.record_call("yfinance", "get_quote", True, 10.0)
        health.note_cache("yfinance.get_quote", "hit")
    assert health.snapshot() == {}
    health.record_call("yfinance", "get_quote", True, 10.0)
    assert health.snapshot()["yfinance"].calls == 1


def test_recording_never_raises(monkeypatch):
    monkeypatch.setattr(health, "_clock", lambda: (_ for _ in ()).throw(RuntimeError("clock broke")))
    health.record_call("x", "y", True, 1.0)  # must not raise
    health.note_cache("x.y", "hit")


def test_track_records_success_and_failure_and_reraises():
    with health.track("fred", "get_series"):
        pass
    with pytest.raises(DataProviderError):
        with health.track("fred", "get_series"):
            raise DataProviderError("no")
    snap = health.snapshot()["fred"]
    assert (snap.successes, snap.failures, snap.last_error) == (1, 1, "no")


class _Cached:
    name = "cachedprov"

    def __init__(self):
        self.fail = False

    @cached(60)
    def get_quote(self, symbol):
        if self.fail:
            raise DataProviderError("upstream down")
        return {"symbol": symbol}


class _Plain:
    name = "plainprov"

    def get_quote(self, symbol):
        raise DataProviderError("nope")

    def get_news(self, symbol, limit=5):
        raise NotImplementedError


def test_composite_records_each_call_and_ignores_unimplemented_methods():
    composite = CompositeDataProvider([_Plain(), _Cached()])
    assert composite.get_quote("SPY") == {"symbol": "SPY"}
    snaps = health.snapshot()
    assert snaps["plainprov"].failures == 1
    assert "nope" in snaps["plainprov"].last_error
    assert snaps["cachedprov"].successes == 1

    with pytest.raises(AllProvidersFailedError):
        CompositeDataProvider([_Plain()]).get_news("SPY")
    # NotImplementedError is "this provider doesn't offer that", not a call.
    assert {m.method for m in health.snapshot()["plainprov"].methods} == {"get_quote"}


def test_cache_hits_are_counted_but_are_not_latency_samples():
    prov = _Cached()
    composite = CompositeDataProvider([prov])
    composite.get_quote("SPY")  # miss: reaches the source
    composite.get_quote("SPY")  # hit
    composite.get_quote("SPY")  # hit
    snap = health.snapshot()["cachedprov"]
    assert snap.calls == 3
    assert snap.latency_samples == 1
    assert snap.cache_hits == 2 and snap.cache_lookups == 3
    assert snap.cache_hit_ratio == pytest.approx(2 / 3)


def test_stale_answers_are_counted_and_degrade_the_status():
    prov = _Cached()
    composite = CompositeDataProvider([prov])
    composite.get_quote("SPY")
    # Expire the fresh entry but keep last-known-good, then fail the source.
    from app.data_providers import cache as cache_module

    cache_module._store.clear()
    prov.fail = True
    assert composite.get_quote("SPY") == {"symbol": "SPY"}
    snap = health.snapshot()["cachedprov"]
    assert snap.stale_served == 1
    assert snap.stale_in_window == 1
    assert snap.status == health.STATUS_DEGRADED
