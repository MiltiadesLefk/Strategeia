from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime
from app.schemas.portfolio_schemas import PortfolioStatsSchema


class SleeveSchema(BaseModel):
    id: int
    key: str
    name: str
    style: str
    starting_cash: float
    enabled: bool
    color: str | None = None
    notes: str | None = None
    created_at: UtcDatetime
    is_core: bool = False


class SleeveWithStatsSchema(SleeveSchema):
    """A sleeve and its live numbers, computed from that sleeve's own rows."""

    stats: PortfolioStatsSchema


class SleeveCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    style: str = Field(min_length=1, max_length=40)
    starting_cash: float = Field(default=100_000.0, gt=0, le=1_000_000_000)
    notes: str | None = Field(default=None, max_length=500)


class SleeveUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    style: str | None = Field(default=None, min_length=1, max_length=40)
    enabled: bool | None = None
    notes: str | None = Field(default=None, max_length=500)
