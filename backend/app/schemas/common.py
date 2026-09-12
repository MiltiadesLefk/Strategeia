from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import PlainSerializer


def to_utc_iso(value: datetime) -> str:
    """Serialize a datetime as an explicitly UTC ISO-8601 string ending in Z.

    Every datetime in this app is stored naive-but-UTC (SQLite columns are
    naive — see timeutil.utcnow_naive). Pydantic's default serializer emits
    those as a bare "2026-09-12T16:16:39" with no offset, and JavaScript's
    `new Date()` reads a bare ISO string as LOCAL time — so every timestamp
    the frontend rendered was silently shifted by the viewer's UTC offset,
    which also made "x minutes ago" relative times wrong (and, east of UTC,
    able to read as the future). Stamping the offset here fixes it once, at
    the boundary, for every consumer instead of per-component in the UI.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# Use in place of `datetime` on any response schema field. Parsing is
# unchanged — this only affects how the value is written out.
UtcDatetime = Annotated[datetime, PlainSerializer(to_utc_iso, return_type=str, when_used="json")]
