from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.deps import get_session, require_auth
from app.data_providers.universe_store import SYMBOL_PATTERN
from app.knowledge.research_library import LIBRARY_KINDS, MAX_LIMIT
from app.schemas.library_schemas import LibraryResponse
from app.services.library_service import get_library

router = APIRouter(prefix="/api/library", tags=["library"], dependencies=[Depends(require_auth)])


@router.get("/{symbol}", response_model=LibraryResponse)
def library_for_symbol(
    symbol: str,
    kind: list[str] | None = Query(None, description=f"Only these kinds (repeat to give several): {', '.join(LIBRARY_KINDS)}"),
    q: str | None = Query(None, max_length=100, description="Text to look for in titles and summaries"),
    since: datetime | None = Query(None, description="Only entries known at or after this time"),
    limit: int = Query(100, ge=1, le=MAX_LIMIT),
    session: Session = Depends(get_session),
) -> LibraryResponse:
    """One ticker's dated history (news, fundamentals, filings, insider and Congress trades, watcher events,
    lessons). Read-only: this never fetches data and never writes."""
    symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.match(symbol):
        raise HTTPException(status_code=422, detail="Not a valid ticker symbol")
    unknown = [k for k in (kind or []) if k not in LIBRARY_KINDS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown kind: {', '.join(unknown)}")
    return get_library(session, symbol, kinds=kind, query=q, since=since, limit=limit)
