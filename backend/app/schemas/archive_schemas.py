from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class ArchivedNewsSchema(BaseModel):
    headline: str
    publisher: str
    url: str
    # The provider's own string, as stored (may be empty).
    published_at: str
    # When it became public (its own publish time, else when we fetched it).
    known_at: UtcDatetime
    # "source" = the item's own publish time; "fetched" = our fetch time,
    # because the provider gave no time we could trust.
    known_at_basis: str
    fetched_at: UtcDatetime


class ArchivedFundamentalsSchema(BaseModel):
    known_at: UtcDatetime
    fetched_at: UtcDatetime
    revenue_ttm: float | None
    eps_ttm: float | None
    market_cap: float | None
    latest_fiscal_year: int | None
    financial_years: int


class ArchiveTotals(BaseModel):
    """The archive as a whole, across every symbol."""

    news_count: int
    fundamentals_count: int
    symbols: int
    first_archived_at: UtcDatetime | None
    last_archived_at: UtcDatetime | None


class ArchiveResponse(BaseModel):
    symbol: str
    news_count: int
    fundamentals_count: int
    # When we FIRST saved anything for this symbol (the earliest date a
    # backtest can rely on the archive for it), and the most recent save.
    first_archived_at: UtcDatetime | None
    last_archived_at: UtcDatetime | None
    recent_news: list[ArchivedNewsSchema]
    recent_fundamentals: list[ArchivedFundamentalsSchema]
    totals: ArchiveTotals
