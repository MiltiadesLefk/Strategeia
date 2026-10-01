from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime


class WatchlistEntry(BaseModel):
    symbol: str
    name: str
    sector: str
    # False when the sector is "Unknown": the sector-concentration rule has no
    # opinion on such a symbol, and the UI says so instead of showing a label.
    sector_known: bool


class WatchlistResponse(BaseModel):
    # Which layer decides what runs right now: the STRATEGEIA_DEV_TICKERS env
    # var, the list saved in the app, or the bundled CSV.
    active_layer: Literal["dev_filter", "custom", "bundled"]
    # What every screen, scan and auto-scan actually uses (in scan order).
    entries: list[WatchlistEntry]
    # What the Settings editor works on: the saved list when there is one, else
    # the bundled list. Differs from `entries` only while the dev filter is on.
    editable_entries: list[WatchlistEntry]
    has_custom: bool
    custom_updated_at: UtcDatetime | None = None
    # Set when a saved file exists but could not be read (the bundled list is
    # being used instead).
    custom_error: str | None = None
    dev_filter: list[str] | None = None
    bundled_size: int
    min_symbols: int
    max_symbols: int
    # The "scan_universe_size" setting and what it means for this list: scans
    # take the first N symbols, so only `scanned_count` of `entries` are covered.
    scan_universe_size: int
    scanned_count: int


class WatchlistSaveRequest(BaseModel):
    # Symbols are validated in the service (a plain message listing every
    # problem beats a 422 for the first bad item), so no pattern here.
    symbols: list[str] = Field(default_factory=list)


class SymbolCheckRequest(BaseModel):
    symbol: str


class SymbolCheckResponse(BaseModel):
    symbol: str
    valid: bool
    name: str | None = None
    sector: str | None = None
    sector_known: bool = False
    # True when the symbol is in the bundled catalogue (name and sector are
    # known without asking a data provider).
    in_catalogue: bool = False
    message: str | None = None
