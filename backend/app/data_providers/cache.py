from __future__ import annotations

import functools
import time
from threading import Lock

_store: dict[tuple, tuple[float, object]] = {}
_lock = Lock()


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


def cached(ttl_seconds: float):
    """Method decorator: caches by (qualified method name, self.name, args, kwargs)."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            prefix = f"{getattr(self, 'name', type(self).__name__)}.{func.__name__}"
            cached_value = cache_get(prefix, args, kwargs)
            if cached_value is not None:
                return cached_value
            value = func(self, *args, **kwargs)
            cache_set(prefix, args, kwargs, value, ttl_seconds)
            return value

        return wrapper

    return decorator


def clear_cache() -> None:
    with _lock:
        _store.clear()
