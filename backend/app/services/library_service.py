"""The read side of the research library for the API: filters, counts and a response."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session

from app.knowledge.research_library import LIBRARY_KINDS, MAX_LIMIT, library_entries_as_of
from app.schemas.library_schemas import LibraryEntrySchema, LibraryResponse

LIBRARY_NOTE = (
    "Only what this app recorded since it began saving data. An empty list means nothing was recorded, "
    "not that nothing happened. News, filings and notes are quoted from outside sources."
)


def get_library(
    session: Session,
    symbol: str,
    kinds: list[str] | None = None,
    query: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> LibraryResponse:
    """Read-only: never fetches data and never writes."""
    symbol = symbol.strip().upper()
    everything = library_entries_as_of(session, symbol, kinds=kinds, query=query, since=since, limit=MAX_LIMIT * len(LIBRARY_KINDS))
    counts: dict[str, int] = {}
    for entry in everything:
        counts[entry.kind] = counts.get(entry.kind, 0) + 1
    shown = everything[: max(1, min(limit, MAX_LIMIT))]
    return LibraryResponse(
        symbol=symbol,
        counts=counts,
        total=len(everything),
        entries=[LibraryEntrySchema(**e.__dict__) for e in shown],
        note=LIBRARY_NOTE,
    )
