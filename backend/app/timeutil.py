from __future__ import annotations

from datetime import datetime, timezone


def utcnow_naive() -> datetime:
    """UTC 'now' as a naive datetime (SQLite columns are naive) without
    using the deprecated datetime.utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_from_timestamp_naive(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def utc_iso_from_timestamp(ts: float) -> str:
    """Unix timestamp -> ISO-8601 string with an explicit Z.

    News `published_at` reaches the frontend as a plain string, so it misses
    the schema-level UtcDatetime serializer. Emitting it naive meant
    JavaScript parsed it as local time and formatRelativeTime drifted by the
    viewer's UTC offset — same bug as the datetime columns, different path.
    """
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
