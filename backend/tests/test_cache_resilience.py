"""Stale-while-revalidate and single-flight behaviour of the provider cache.

The tension this file pins down: serving last-known-good data on a provider
outage is what keeps the Research page usable when Yahoo is down, but it must
never reach code that makes a trading decision. A stop evaluated against a
bar from two days ago is exactly the stale-data failure the multi-bar exit
rewrite exists to prevent.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.data_providers.cache import cached, clear_cache, fresh_data_only


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


class FlakyProvider:
    name = "flaky"

    def __init__(self):
        self.calls = 0
        self.fail = False

    @cached(60)
    def fetch(self, symbol: str) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return f"{symbol}-value-{self.calls}"


def test_successful_value_is_cached():
    p = FlakyProvider()
    assert p.fetch("AAPL") == "AAPL-value-1"
    assert p.fetch("AAPL") == "AAPL-value-1"
    assert p.calls == 1


def test_stale_value_is_served_when_the_provider_fails():
    """The Research-page case: Yahoo goes down, the page keeps rendering the
    last real numbers instead of blanking."""
    p = FlakyProvider()
    p.fetch("AAPL")

    # Force the TTL to lapse, then break the provider.
    from app.data_providers import cache as cache_module

    cache_module._store.clear()
    p.fail = True

    assert p.fetch("AAPL") == "AAPL-value-1"  # stale, but real and previously true


def test_stale_data_is_refused_inside_fresh_data_only():
    """The exit-engine case: a failed fetch must propagate so the caller skips
    the position, never close a trade against an old price."""
    p = FlakyProvider()
    p.fetch("AAPL")

    from app.data_providers import cache as cache_module

    cache_module._store.clear()
    p.fail = True

    with pytest.raises(RuntimeError):
        with fresh_data_only():
            p.fetch("AAPL")


def test_fresh_data_only_scope_is_restored_afterwards():
    p = FlakyProvider()
    p.fetch("AAPL")

    from app.data_providers import cache as cache_module

    cache_module._store.clear()
    p.fail = True

    with pytest.raises(RuntimeError):
        with fresh_data_only():
            p.fetch("AAPL")

    # Outside the block, stale service resumes.
    assert p.fetch("AAPL") == "AAPL-value-1"


def test_failure_with_no_prior_success_still_raises():
    """Stale fallback must not invent a value it never had."""
    p = FlakyProvider()
    p.fail = True
    with pytest.raises(RuntimeError):
        p.fetch("NEVERFETCHED")


class SlowProvider:
    name = "slow"

    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    @cached(60)
    def fetch(self, symbol: str) -> str:
        with self._lock:
            self.calls += 1
        time.sleep(0.15)
        return f"{symbol}-done"


def test_concurrent_callers_collapse_into_one_upstream_request():
    """Single-flight. An auto-scan pass asks for SPY and ^VIX once per symbol;
    on a cold cache that is N simultaneous identical requests, which is how a
    free provider starts answering 429."""
    p = SlowProvider()
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(p.fetch("SPY"))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == ["SPY-done"] * 8
    assert p.calls == 1, f"expected one upstream call, got {p.calls}"


def test_different_keys_are_not_collapsed():
    p = SlowProvider()
    threads = [threading.Thread(target=p.fetch, args=(s,)) for s in ("AAPL", "MSFT", "NVDA")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert p.calls == 3
