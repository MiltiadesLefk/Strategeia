from __future__ import annotations

import functools
import time
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock

# Fresh entries, subject to TTL.
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


@contextmanager
def fresh_data_only():
    """Within this block, providers never serve stale data on failure —
    a failed fetch propagates so the caller can skip rather than act on an
    old price. Use for any code path that makes a trading decision."""
    token = _fresh_only.set(True)
    try:
        yield
    finally:
        _fresh_only.reset(token)


def _make_key(prefix: str, args: tuple, kwargs: dict) -> tuple:
    return (prefix, args, tuple(sorted(kwargs.items())))


def cache_get(prefix: str, args: tuple, kwargs: dict):
    key = _make_key(prefix, args, kwargs)
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del _store[key]
            return None
        return value


def cache_set(prefix: str, args: tuple, kwargs: dict, value: object, ttl_seconds: float) -> None:
    key = _make_key(prefix, args, kwargs)
    with _lock:
        _store[key] = (time.monotonic() + ttl_seconds, value)
        _remember_stale(key, value)


def _remember_stale(key: tuple, value: object) -> None:
    """Caller must hold _lock."""
    if key not in _stale and len(_stale) >= _STALE_MAX_ENTRIES:
        _stale.pop(next(iter(_stale)), None)  # oldest insertion first
    _stale[key] = value


def stale_get(prefix: str, args: tuple, kwargs: dict):
    key = _make_key(prefix, args, kwargs)
    with _lock:
        return _stale.get(key)


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
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            prefix = f"{getattr(self, 'name', type(self).__name__)}.{func.__name__}"
            cached_value = cache_get(prefix, args, kwargs)
            if cached_value is not None:
                return cached_value

            key = _make_key(prefix, args, kwargs)
            with _lock_for(key):
                # Re-check: whoever held the lock before us may have filled it.
                cached_value = cache_get(prefix, args, kwargs)
                if cached_value is not None:
                    return cached_value
                try:
                    value = func(self, *args, **kwargs)
                except Exception:
                    if stale_on_error and not _fresh_only.get():
                        fallback = stale_get(prefix, args, kwargs)
                        if fallback is not None:
                            return fallback
                    raise
                cache_set(prefix, args, kwargs, value, ttl_seconds)
                return value

        return wrapper

    return decorator


def clear_cache() -> None:
    with _lock:
        _store.clear()
        _stale.clear()
    with _key_locks_guard:
        _key_locks.clear()
