from __future__ import annotations

from datetime import datetime, timezone


def utcnow_naive() -> datetime:
    """UTC 'now' as a naive datetime (SQLite columns are naive) without
    using the deprecated datetime.utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_from_timestamp_naive(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
