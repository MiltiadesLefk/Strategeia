from __future__ import annotations

import atexit
import functools
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock

from app.data_providers import health
from app.data_providers.cache_store import PersistentCacheStats, PersistentCacheStore

logger = logging.getLogger(__name__)

# Fresh entries, subject to TTL. Expiry here is on the monotonic clock: it is
# the in-process layer, immune to the wall clock being adjusted.
_store: dict[tuple, tuple[float, object]] = {}
# Last-known-good value per key, with NO expiry. Only ever read when the live
# call fails — see the `cached` docstring. Bounded by _STALE_MAX_ENTRIES so a
# long-running process can't accumulate one row per symbol per method forever.
_stale: dict[tuple, object] = {}
_lock = Lock()

_STALE_MAX_ENTRIES = 2_000

# One lock per cache key, so concurrent callers asking for the SAME thing
# collapse into a single upstream request instead of stampeding the provider.
_key_locks: dict[tuple, Lock] = {}
_key_locks_guard = Lock()

# When true, a failing call raises instead of falling back to stale data.
# The paper-trading exit scan sets this: evaluating a stop against a
# last-known-good bar from days ago would close positions on prices the
# market has long since left behind, which is the exact class of bug the
# multi-bar exit rewrite exists to prevent. Stale data is fine for *showing*
# a research page; it is not fine for *deciding* an exit.
_fresh_only: ContextVar[bool] = ContextVar("fresh_only", default=False)

# The on-disk layer (see cache_store.py). Resolved lazily on first use so that
# importing this module never touches the filesystem; None means memory-only.
# A persisted TTL is wall-clock (a monotonic clock can't survive a restart),
# which is why _wall_clock exists separately from the monotonic one above.
_wall_clock = time.time
_persist: PersistentCacheStore | None = None
_persist_resolved = False
_persist_guard = Lock()

# Counters since process start, for the operator's cache card. Plain ints under
# a lock: they are read rarely and bumped on every cache call.
_COUNTER_NAMES = (
    "memory_hits",  # served from the in-process layer
    "disk_hits",  # not in memory, served from the file (a warm start)
    "coalesced_hits",  # waited on another thread's fetch of the same key
    "misses",  # went to the provider
    "stale_served",  # provider failed, last-known-good from memory
    "stale_served_from_disk",  # provider failed, last-known-good from the file
    "fetch_failures",  # provider failed and nothing could be served
)
_counters: dict[str, int] = dict.fromkeys(_COUNTER_NAMES, 0)
_counters_lock = Lock()


def _count(name: str) -> None:
    with _counters_lock:
        _counters[name] += 1


def reset_cache_counters() -> None:
    with _counters_lock:
        for name in _counters:
            _counters[name] = 0


def _default_persistent_store() -> PersistentCacheStore | None:
    """The store the settings ask for, or None. Never raises: a misconfigured or
    unwritable location just means a memory-only cache, as before."""
    try:
        # InfraSettings(), not get_infra_settings(): the latter generates and
        # writes the auth secrets, which a cache lookup has no business doing.
        from app.config import InfraSettings

        infra = InfraSettings()
        if not infra.persist_cache_db:
            return None
        store = PersistentCacheStore(infra.cache_db_file)
        return store if store.enabled else None
    except Exception as exc:
        logger.warning("Persistent provider cache unavailable, using memory only: %s", exc)
        return None


def _persistent() -> PersistentCacheStore | None:
    global _persist, _persist_resolved
    if _persist_resolved:
        return _persist
    with _persist_guard:
        if not _persist_resolved:
            _persist = _default_persistent_store()
            _persist_resolved = True
    return _persist


def configure_persistence(store: PersistentCacheStore | None) -> None:
    """Use `store` as the on-disk layer (None = memory only), replacing whatever
    was resolved before. The memory layer is left alone. Tests and scripts call
    this; the app normally lets the settings decide."""
    global _persist, _persist_resolved
    with _persist_guard:
        previous = _persist
        _persist = store
        _persist_resolved = True
    if previous is not None and previous is not store:
        previous.close()


def flush_persistent_cache() -> None:
    store = _persist
    if store is not None:
        store.flush()


# Writes are queued for the background writer; make sure the last ones land.
atexit.register(flush_persistent_cache)


@contextmanager
def fresh_data_only():
    """Within this block, providers never serve stale data on failure —
    a failed fetch propagates so the caller can skip rather than act on an
    old price. Use for any code path that makes a trading decision.

    This covers the persisted layer too: an expired file entry is never a hit
    (a hit needs an unexpired row), and last-known-good is only read by the
    stale-on-error fallback, which this scope switches off."""
    token = _fresh_only.set(True)
    try:
        yield
    finally:
        _fresh_only.reset(token)


def _make_key(prefix: str, args: tuple, kwargs: dict) -> tuple:
    return (prefix, args, tuple(sorted(kwargs.items())))


def _key_text(key: tuple) -> str:
    """The key as stored in the file. Arguments are symbols, periods and small
    numbers, so repr() is stable across runs."""
    return repr(key)


def _lookup(key: tuple) -> tuple[object, str | None]:
    """-> (value, "memory" | "disk" | None). A disk hit is promoted into memory
    with whatever TTL it has left, so the file is read once per key per run."""
    with _lock:
        entry = _store.get(key)
        if entry is not None:
            expires_at, value = entry
            if time.monotonic() <= expires_at:
                return value, "memory"
            del _store[key]
    persistent = _persistent()
    if persistent is None:
        return None, None
    found, value, wall_expires_at = persistent.get(_key_text(key))
    if not found or value is None:
        return None, None
    remaining = wall_expires_at - _wall_clock()
    if remaining <= 0:  # the file said fresh a moment ago; the clock moved on
        return None, None
    with _lock:
        _store[key] = (time.monotonic() + remaining, value)
        _remember_stale(key, value)
    return value, "disk"


def cache_get(prefix: str, args: tuple, kwargs: dict):
    return _lookup(_make_key(prefix, args, kwargs))[0]


def cache_set(prefix: str, args: tuple, kwargs: dict, value: object, ttl_seconds: float) -> None:
    key = _make_key(prefix, args, kwargs)
    with _lock:
        _store[key] = (time.monotonic() + ttl_seconds, value)
        _remember_stale(key, value)
    persistent = _persistent()
    # None is never a cache hit (see `cached`), so there is nothing to persist.
    if persistent is not None and value is not None:
        persistent.put(_key_text(key), prefix, value, _wall_clock() + ttl_seconds)


def _remember_stale(key: tuple, value: object) -> None:
    """Caller must hold _lock."""
    if key not in _stale and len(_stale) >= _STALE_MAX_ENTRIES:
        _stale.pop(next(iter(_stale)), None)  # oldest insertion first
    _stale[key] = value


def _stale_lookup(key: tuple) -> tuple[object, str | None]:
    with _lock:
        value = _stale.get(key)
    if value is not None:
        return value, "memory"
    persistent = _persistent()
    if persistent is None:
        return None, None
    found, value, _expires_at = persistent.get(_key_text(key), allow_expired=True)
    if not found or value is None:
        return None, None
    with _lock:
        _remember_stale(key, value)
    return value, "disk"


def stale_get(prefix: str, args: tuple, kwargs: dict):
    return _stale_lookup(_make_key(prefix, args, kwargs))[0]


def _lock_for(key: tuple) -> Lock:
    with _key_locks_guard:
        existing = _key_locks.get(key)
        if existing is None:
            existing = Lock()
            _key_locks[key] = existing
        return existing


def cached(ttl_seconds: float, *, stale_on_error: bool = True):
    """Method decorator: caches by (qualified method name, self.name, args, kwargs).

    Three behaviours, each earning its keep:

    1. **TTL cache** — the original behaviour.
    2. **Single-flight** — concurrent callers for the same key wait on one
       upstream request rather than each firing their own. An auto-scan pass
       asks for SPY and ^VIX once per symbol; without this, a cold cache means
       50 simultaneous identical requests, which is how a free provider starts
       returning 429s.
    3. **Stale-on-error** — on failure, the last successful value is returned
       instead of raising. This is what keeps a Yahoo outage from blanking the
       Research page. It is NOT fabricated data: it is real data that was true
       at a knowable earlier time, and the UI stamps when it arrived. Callers
       that make trading decisions opt out via `fresh_data_only()`.

    All three hold across a restart when the persisted layer is on (see
    cache_store.py): a fresh start finds yesterday's results in the file instead
    of re-asking Yahoo for all of them, and the last-known-good survives a
    redeploy that happens mid-outage.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            prefix = f"{getattr(self, 'name', type(self).__name__)}.{func.__name__}"
            key = _make_key(prefix, args, kwargs)
            cached_value, source = _lookup(key)
            if cached_value is not None:
                _count("memory_hits" if source == "memory" else "disk_hits")
                health.note_cache(prefix, "hit")
                return cached_value

            with _lock_for(key):
                # Re-check: whoever held the lock before us may have filled it.
                cached_value, _source = _lookup(key)
                if cached_value is not None:
                    _count("coalesced_hits")
                    health.note_cache(prefix, "hit")
                    return cached_value
                _count("misses")
                try:
                    value = func(self, *args, **kwargs)
                except Exception:
                    if stale_on_error and not _fresh_only.get():
                        fallback, fallback_source = _stale_lookup(key)
                        if fallback is not None:
                            _count("stale_served" if fallback_source == "memory" else "stale_served_from_disk")
                            health.note_cache(prefix, "stale")
                            return fallback
                    _count("fetch_failures")
                    health.note_cache(prefix, "miss")
                    raise
                cache_set(prefix, args, kwargs, value, ttl_seconds)
                health.note_cache(prefix, "miss")
                return value

        return wrapper

    return decorator


def clear_cache() -> None:
    """Empty both layers: memory and, when on, the file."""
    with _lock:
        _store.clear()
        _stale.clear()
    with _key_locks_guard:
        _key_locks.clear()
    persistent = _persistent()
    if persistent is not None:
        persistent.clear()


@dataclass
class CacheStats:
    memory_entries: int
    memory_stale_entries: int
    counters: dict[str, int]
    hit_rate: float | None  # hits / (hits + misses), None before the first lookup
    persistent: PersistentCacheStats | None  # None = persistence is off


def cache_stats() -> CacheStats:
    """A read-only snapshot for the operator's cache card."""
    with _lock:
        memory_entries = len(_store)
        stale_entries = len(_stale)
    with _counters_lock:
        counters = dict(_counters)
    hits = counters["memory_hits"] + counters["disk_hits"] + counters["coalesced_hits"]
    lookups = hits + counters["misses"]
    persistent = _persistent()
    return CacheStats(
        memory_entries=memory_entries,
        memory_stale_entries=stale_entries,
        counters=counters,
        hit_rate=(hits / lookups) if lookups else None,
        persistent=persistent.stats() if persistent is not None else None,
    )
