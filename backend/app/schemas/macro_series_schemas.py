from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class MacroSeriesInfo(BaseModel):
    series_id: str
    name: str
    unit: str
    source: str  # fred | ecb
    frequency: str
    description: str


class MacroPoint(BaseModel):
    date: date
    value: float


class MacroSeriesResponse(BaseModel):
    series_id: str
    name: str
    unit: str
    source: str
    frequency: str
    points: list[MacroPoint]
    # When this server fetched the series. The newest observation can be much
    # older (a monthly series ends last month): see last_observation_date.
    last_updated: UtcDatetime | None = None
    last_observation_date: date | None = None
