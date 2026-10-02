from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime

MAX_SCREEN_SYMBOLS = 200
MAX_SAVED_SCREENS = 50
MAX_SCREEN_NAME_LENGTH = 60


class FilterIn(BaseModel):
    field: str = Field(max_length=40)
    op: str = Field(max_length=10)
    # A number for numeric fields, text for text fields. `value2` is the upper bound of "between".
    value: float | str | None = None
    value2: float | None = None


class ScreenSpec(BaseModel):
    """Everything that defines a screen apart from which symbols it runs over."""

    filters: list[FilterIn] = Field(default_factory=list, max_length=12)
    sort_field: str | None = Field(default="scanner_score", max_length=40)
    sort_dir: Literal["asc", "desc"] = "desc"
    # How many matching rows to return.
    limit: int = Field(default=50, ge=1, le=MAX_SCREEN_SYMBOLS)
    # Extra fields to show; the ones a rule or the sort uses are always included.
    columns: list[str] = Field(default_factory=list, max_length=20)


class ScreenerRunRequest(ScreenSpec):
    # Defaults to the active symbol list.
    symbols: list[str] | None = Field(default=None, max_length=MAX_SCREEN_SYMBOLS)
    # At most this many symbols are read (the first N, in list order). The default keeps one run to
    # the cached scan-sized set instead of fetching a whole index.
    scan_cap: int = Field(default=60, ge=1, le=MAX_SCREEN_SYMBOLS)


class ScreenerFieldOut(BaseModel):
    name: str
    label: str
    kind: Literal["number", "text"]
    unit: str
    source: str
    description: str
    operators: list[str]
    # Needs an extra fetch per symbol, so it is only loaded when a rule, the sort or a column uses it.
    costly: bool


class ScreenerFieldsResponse(BaseModel):
    fields: list[ScreenerFieldOut]
    max_filters: int
    max_symbols: int
    default_scan_cap: int


class ScreenerRow(BaseModel):
    symbol: str
    name: str
    as_of: str | None = None
    # Field name -> value (number or text), null where it could not be worked out.
    values: dict[str, float | str | None]


class ScreenerRunResponse(BaseModel):
    rows: list[ScreenerRow]
    # Symbols read out of the symbols considered, and the size of the list the run drew from.
    scanned: int
    universe_size: int
    # Rows that passed every rule (before the row limit).
    matched: int
    # Rows dropped only because a rule's field was missing for them.
    skipped_missing_data: int
    # Symbols whose price data no provider could supply.
    missing: list[str]
    columns: list[str]
    sort_field: str | None = None
    sort_dir: Literal["asc", "desc"] = "desc"


class SavedScreenCreate(ScreenSpec):
    name: str = Field(min_length=1, max_length=MAX_SCREEN_NAME_LENGTH)


class SavedScreen(SavedScreenCreate):
    id: str
    created_at: UtcDatetime
