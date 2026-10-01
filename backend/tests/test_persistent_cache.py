"""The provider cache's on-disk layer: warm starts, TTL, stale-on-error and
fresh_data_only across a restart, plus the file's own safety properties (corrupt
rows, size cap, concurrent writers, a broken disk).

A "restart" here is: flush the file, empty the in-memory layer, and open a NEW
store on the same path, which is exactly the state a fresh process starts in.
Time is a fake wall clock so TTLs can lapse without sleeping.
"""

from __future__ import annotations

import os
import sqlite3
import threading

import pandas as pd
import pytest

from app.data_providers import cache as cache_module
from app.data_providers.base import QuoteData
from app.data_providers.cache import (
    cache_stats,
    cached,
    clear_cache,
    configure_persistence,
    fresh_data_only,
    reset_cache_counters,
)
from app.data_providers.cache_store import PersistentCacheStore


class Clock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(cache_module, "_wall_clock", fake)
    return fake


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "cache.db"


@pytest.fixture
def disk(db_path, clock):
    """A persistent layer on a tmp file, writing synchronously. Yields a
    `restart()` that simulates a process restart against the same file."""
    state = {"store": None}

    def open_store(**kwargs):
        store = PersistentCacheStore(db_path, clock=clock, write_behind=False, **kwargs)
        state["store"] = store
        configure_persistence(store)
        return store

    def restart(**kwargs):
        state["store"].flush()
        with cache_module._lock:
            cache_module._store.clear()
            cache_module._stale.clear()
        return open_store(**kwargs)

    open_store()
    clear_cache()
    reset_cache_counters()
    restart.store = lambda: state["store"]
    yield restart
    configure_persistence(None)
    clear_cache()
    reset_cache_counters()


class Provider:
    name = "fake"

    def __init__(self):
        self.calls = 0
        self.fail = False

    @cached(60)
    def quote(self, symbol: str) -> QuoteData:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return QuoteData(symbol, 100.0 + self.calls, 0.5, 1e6, 9e5)

    @cached(60)
    def bars(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        self.calls += 1
        dates = pd.date_range("2026-09-01", periods=4, freq="B", tz="America/New_York")
        return pd.DataFrame({"date": dates, "close": [1.0, 2.0, 3.0, 4.0], "volume": [5, 6, 7, 8]})

    @cached(60)
    def nothing(self, symbol: str):
        self.calls += 1
        return None


def test_a_restart_starts_warm(disk):
    p = Provider()
    first = p.quote("AAPL")
    assert p.calls == 1
    disk()
    again = p.quote("AAPL")
    assert p.calls == 1, "a fresh process refetched what the file already had"
    assert again == first and isinstance(again, QuoteData)
    assert cache_stats().counters["disk_hits"] == 1
    p.quote("AAPL")  # now promoted into memory
    assert cache_stats().counters["memory_hits"] == 1


def test_dataframes_come_back_intact_after_a_restart(disk):
    p = Provider()
    original = p.bars("AAPL", period="1y")
    disk()
    restored = p.bars("AAPL", period="1y")
    assert p.calls == 1
    pd.testing.assert_frame_equal(restored, original)


def test_different_arguments_are_different_entries(disk):
    p = Provider()
    p.bars("AAPL", period="1y")
    p.bars("AAPL", period="6mo")
    disk()
    p.bars("AAPL", period="1y")
    p.bars("AAPL", period="6mo")
    assert p.calls == 2


def test_ttl_is_wall_clock_across_a_restart(disk, clock):
    p = Provider()
    p.quote("AAPL")
    clock.now += 30
    disk()
    p.quote("AAPL")
    assert p.calls == 1, "30s into a 60s TTL is still fresh after a restart"

    clock.now += 31  # 61s after it was stored
    disk()
    p.quote("AAPL")
    assert p.calls == 2, "an expired file entry must be refetched, not served"


def test_a_warm_entry_expires_when_its_remaining_ttl_runs_out(disk, clock):
    """Loaded from disk with 30s left, the in-memory copy must not live longer than that."""
    p = Provider()
    p.quote("AAPL")
    clock.now += 30
    disk()
    p.quote("AAPL")  # disk hit, promoted with ~30s left
    (expires_at, _value), = cache_module._store.values()
    import time

    assert 25 < expires_at - time.monotonic() <= 30


def test_stale_on_error_survives_a_restart(disk, clock):
    """A Yahoo outage plus a redeploy: the last real quote is still what the page shows."""
    p = Provider()
    original = p.quote("AAPL")
    clock.now += 3600  # long expired
    disk()
    p.fail = True
    assert p.quote("AAPL") == original
    assert cache_stats().counters["stale_served_from_disk"] == 1


def test_fresh_data_only_never_gets_an_expired_or_stale_persisted_entry(disk, clock):
    p = Provider()
    p.quote("AAPL")
    clock.now += 3600
    disk()
    p.fail = True
    with pytest.raises(RuntimeError):
        with fresh_data_only():
            p.quote("AAPL")
    # Outside the scope the same call is served stale, so the file did hold it.
    assert p.quote("AAPL").symbol == "AAPL"


def test_fresh_data_only_still_takes_an_unexpired_persisted_hit(disk):
    p = Provider()
    p.quote("AAPL")
    disk()
    p.fail = True
    with fresh_data_only():
        assert p.quote("AAPL").symbol == "AAPL"  # fresh, so it is a legitimate hit
    assert p.calls == 1


def test_failure_with_nothing_persisted_still_raises(disk):
    p = Provider()
    p.fail = True
    with pytest.raises(RuntimeError):
        p.quote("NEVER")


def test_none_results_are_not_persisted(disk):
    p = Provider()
    p.nothing("X")
    disk.store().flush()
    assert disk.store().stats().entries == 0


def test_clear_cache_empties_memory_and_disk(disk):
    p = Provider()
    p.quote("AAPL")
    disk.store().flush()
    assert disk.store().stats().entries == 1
    clear_cache()
    assert disk.store().stats().entries == 0
    disk()
    p.quote("AAPL")
    assert p.calls == 2


def test_unencodable_results_stay_memory_only(disk):
    class Opaque:
        pass

    class Odd:
        name = "odd"
        calls = 0

        @cached(60)
        def get(self, symbol):
            Odd.calls += 1
            return Opaque()

    o = Odd()
    first = o.get("X")
    assert o.get("X") is first  # memory cache works
    disk.store().flush()
    stats = disk.store().stats()
    assert stats.entries == 0 and stats.skipped_unencodable == 1


def test_persistence_off_leaves_behaviour_and_disk_alone(tmp_path, clock):
    configure_persistence(None)
    clear_cache()
    p = Provider()
    p.quote("AAPL")
    p.quote("AAPL")
    assert p.calls == 1
    p.fail = True
    cache_module._store.clear()
    assert p.quote("AAPL").symbol == "AAPL"  # in-memory stale-on-error unchanged
    assert cache_stats().persistent is None
    assert list(tmp_path.iterdir()) == []


def test_default_store_follows_the_settings(tmp_path, monkeypatch):
    """PERSIST_CACHE_DB off -> memory only; on -> runtime/cache.db beside settings.json."""
    monkeypatch.setattr(cache_module, "_persist", None)
    monkeypatch.setattr(cache_module, "_persist_resolved", False)
    monkeypatch.setenv("PERSIST_CACHE_DB", "false")
    assert cache_module._default_persistent_store() is None

    monkeypatch.setenv("PERSIST_CACHE_DB", "true")
    monkeypatch.setenv("SETTINGS_PATH", str(tmp_path / "rt" / "settings.json"))
    from app.config import BASE_DIR

    # SETTINGS_PATH is joined onto BASE_DIR; an absolute path replaces it.
    assert BASE_DIR / str(tmp_path / "rt" / "settings.json") == tmp_path / "rt" / "settings.json"
    store = cache_module._default_persistent_store()
    try:
        assert store is not None and store.path == tmp_path / "rt" / "cache.db"
    finally:
        store.close()


def test_a_cache_file_that_is_not_a_database_is_replaced(db_path, clock):
    db_path.write_bytes(b"this is not an sqlite file at all" * 100)
    store = PersistentCacheStore(db_path, clock=clock, write_behind=False)
    try:
        assert store.enabled
        assert store.put("k", "p", [1, 2, 3], clock.now + 60)
        assert store.get("k")[:2] == (True, [1, 2, 3])
    finally:
        store.close()


def test_an_unwritable_location_disables_persistence_without_raising(tmp_path, clock):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    store = PersistentCacheStore(blocker / "cache.db", clock=clock, write_behind=False)  # parent is a file
    assert not store.enabled
    assert store.put("k", "p", 1, clock.now + 60) is False
    assert store.get("k")[0] is False
    store.clear()
    store.flush()
    assert store.stats().enabled is False


def test_a_corrupt_row_is_a_miss_and_is_removed(disk, db_path):
    p = Provider()
    p.quote("AAPL")
    store = disk.store()
    store.flush()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE cache_entry SET value = ?", (b"garbage-not-a-blob",))
    disk()
    p.quote("AAPL")
    assert p.calls == 2, "an undecodable row must fall through to the provider"
    disk.store().flush()
    assert disk.store().stats().entries == 1  # the bad row was replaced by the fresh one


def test_a_corrupt_row_is_not_served_as_stale_either(disk, db_path, clock):
    p = Provider()
    p.quote("AAPL")
    disk.store().flush()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE cache_entry SET value = ?", (b"garbage",))
    clock.now += 3600
    disk()
    p.fail = True
    with pytest.raises(RuntimeError):
        p.quote("AAPL")


def test_a_database_error_never_breaks_a_data_call(disk):
    class BrokenConnection:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("disk I/O error")

        executemany = commit = rollback = execute

        def close(self):
            pass

    p = Provider()
    store = disk.store()
    store._conn = BrokenConnection()
    assert p.quote("AAPL").symbol == "AAPL"  # write fails inside, call succeeds
    with cache_module._lock:
        cache_module._store.clear()
    assert p.quote("AAPL").symbol == "AAPL"  # read fails inside, call succeeds
    assert store.stats().errors >= 1


def test_concurrent_writers_and_readers_share_one_file(db_path, clock):
    """The scheduler thread and request threads hit the same store."""
    store = PersistentCacheStore(db_path, clock=clock, write_behind=True, batch_delay_seconds=0.01)
    errors: list[BaseException] = []

    def worker(n: int):
        try:
            for i in range(30):
                key = f"w{n}-k{i}"
                store.put(key, "p", {"n": n, "i": i}, clock.now + 600)
                found, value, _ = store.get(key)
                assert found and value == {"n": n, "i": i}
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    store.flush()
    try:
        assert not errors, errors
        assert store.stats().entries == 8 * 30
        assert store.stats().errors == 0
    finally:
        store.close()


def test_concurrent_cached_calls_still_single_flight_with_persistence(disk):
    class Slow:
        name = "slow"
        calls = 0

        @cached(60)
        def get(self, symbol):
            import time

            Slow.calls += 1
            time.sleep(0.1)
            return symbol

    s = Slow()
    results = []
    threads = [threading.Thread(target=lambda: results.append(s.get("SPY"))) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["SPY"] * 6 and Slow.calls == 1


def test_size_cap_drops_the_least_recently_used_first(db_path, clock):
    store = PersistentCacheStore(db_path, clock=clock, write_behind=False, max_bytes=60_000)
    try:
        payload = lambda n: os.urandom(1500).hex() + str(n)  # ~3 KB of poorly compressible text
        for i in range(40):
            clock.now += 1
            store.put(f"k{i}", "p", payload(i), clock.now + 10_000)
        # Touch an old key so it is "recently used" and must outlive newer-but-untouched ones.
        clock.now += 1
        assert store.get("k0")[0]
        store.prune()
        stats = store.stats()
        assert stats.payload_bytes <= 60_000
        assert 0 < stats.entries < 40
        assert store.get("k0")[0], "the recently used entry was evicted"
        assert not store.get("k1")[0], "an older untouched entry should have gone first"
        assert store.get("k39")[0], "the newest entry should still be there"
    finally:
        store.close()


def test_rows_older_than_the_stale_limit_are_pruned(db_path, clock):
    store = PersistentCacheStore(db_path, clock=clock, write_behind=False, stale_max_age_seconds=1000)
    try:
        store.put("old", "p", 1, clock.now + 10)
        clock.now += 1500
        store.put("new", "p", 2, clock.now + 10)
        store.prune()
        assert not store.get("old", allow_expired=True)[0]
        assert store.get("new")[0]
    finally:
        store.close()


def test_one_oversized_result_is_not_persisted(db_path, clock):
    store = PersistentCacheStore(db_path, clock=clock, write_behind=False, max_bytes=10_000)
    try:
        assert store.put("big", "p", os.urandom(6000).hex(), clock.now + 60) is False  # > 25% of the cap
        assert store.get("big")[0] is False
    finally:
        store.close()


def test_multi_year_downloads_are_left_to_the_history_store(db_path, clock):
    store = PersistentCacheStore(db_path, clock=clock, write_behind=False)
    try:
        assert store.put("small", "p", os.urandom(20_000).hex(), clock.now + 60) is True
        assert store.put("huge", "p", os.urandom(150_000).hex(), clock.now + 60) is False  # >100 KB compressed
    finally:
        store.close()


def test_queued_writes_are_readable_before_they_are_committed(db_path, clock):
    store = PersistentCacheStore(db_path, clock=clock, write_behind=True, batch_delay_seconds=30)
    try:
        store.put("k", "p", {"a": 1}, clock.now + 60)
        assert store.stats().pending_writes == 1
        assert store.get("k")[:2] == (True, {"a": 1})
        store.flush()
        assert store.stats().pending_writes == 0 and store.stats().entries == 1
    finally:
        store.close()


def test_a_file_from_another_codec_version_is_discarded(db_path, clock):
    store = PersistentCacheStore(db_path, clock=clock, write_behind=False)
    store.put("k", "p", 1, clock.now + 60)
    store.close()
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 99")
    reopened = PersistentCacheStore(db_path, clock=clock, write_behind=False)
    try:
        assert reopened.stats().entries == 0
    finally:
        reopened.close()


def test_stats_counters_and_hit_rate(disk):
    p = Provider()
    p.quote("A")  # miss
    p.quote("A")  # memory hit
    disk()
    p.quote("A")  # disk hit
    stats = cache_stats()
    assert stats.counters["misses"] == 1
    assert stats.counters["memory_hits"] == 1
    assert stats.counters["disk_hits"] == 1
    assert stats.hit_rate == pytest.approx(2 / 3)
    assert stats.persistent is not None and stats.persistent.entries == 1
