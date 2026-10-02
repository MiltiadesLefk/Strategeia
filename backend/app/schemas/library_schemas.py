from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class LibraryEntrySchema(BaseModel):
    kind: str
    kind_label: str
    # When it became public (a lesson: when it was written).
    known_at: UtcDatetime
    title: str
    summary: str
    source: str
    url: str | None = None


class LibraryResponse(BaseModel):
    symbol: str
    # How many entries of each kind matched (before the limit), so the screen can show filter counts.
    counts: dict[str, int]
    total: int
    entries: list[LibraryEntrySchema]
    # Plain-words reminder that an empty library means "not recorded", not "nothing happened".
    note: str
