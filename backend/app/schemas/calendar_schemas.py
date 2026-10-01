from __future__ import annotations

from pydantic import BaseModel


class CalendarItem(BaseModel):
    id: str
    kind: str  # economic | macro | earnings
    title: str
    date: str  # YYYY-MM-DD, US Eastern
    time_et: str | None = None  # HH:MM Eastern; None = all day or time not known
    starts_at: str | None = None  # UTC ISO with Z, when the time is known
    days_until: int  # from today (US Eastern); negative = past
    impact: str | None = None  # Low | Medium | High | Holiday (economic feed only)
    forecast: str | None = None
    previous: str | None = None
    actual: str | None = None
    symbol: str | None = None  # earnings rows
    source: str  # feed | table | provider
    source_label: str
    # Open paper positions this event matters to: the symbol itself for an earnings
    # row, every open position for a market-wide release in the next two weeks.
    position_symbols: list[str] = []
    my_position: bool = False
    # Fed/CPI/jobs rows only: True when the live feed lists the same event the same day.
    confirmed_by_feed: bool | None = None


class CatalystEntry(BaseModel):
    kind: str  # earnings | macro
    title: str
    date: str
    days_until: int


class PositionCatalysts(BaseModel):
    symbol: str
    direction: str
    catalysts: list[CatalystEntry]


class SourceStatus(BaseModel):
    key: str  # economic | macro | earnings
    label: str
    status: str  # ok | partial | unavailable
    detail: str | None = None


class MacroMismatchSchema(BaseModel):
    series: str
    table_date: str | None = None
    feed_date: str | None = None
    message: str


class CalendarResponse(BaseModel):
    from_date: str
    to_date: str
    today: str
    items: list[CalendarItem]
    position_catalysts: list[PositionCatalysts]
    sources: list[SourceStatus]
    mismatches: list[MacroMismatchSchema]
    earnings_symbols_checked: int
    generated_at: str
