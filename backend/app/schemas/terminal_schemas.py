from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class HeatmapTile(BaseModel):
    symbol: str
    name: str
    # Change over the requested window, in percent.
    change_pct: float
    market_cap: float | None = None
    # The tile's area: market cap (a missing one filled with the median) or 1.
    weight: float


class HeatmapSector(BaseModel):
    sector: str
    # Plain average of its tiles' changes (not cap-weighted).
    avg_change_pct: float
    weight: float
    tiles: list[HeatmapTile]


class SectorEtfTile(BaseModel):
    symbol: str
    sector: str
    # None when the ETF's bars could not be fetched.
    change_pct: float | None = None


class HeatmapResponse(BaseModel):
    window: Literal["1d", "5d", "1m"]
    as_of: UtcDatetime
    # Symbols read this time / size of the effective universe / the cap asked for.
    sampled: int
    universe_size: int
    requested_limit: int
    weighting: Literal["market_cap", "equal"]
    sectors: list[HeatmapSector]
    sector_etfs: list[SectorEtfTile]
    # Sampled symbols no provider could supply.
    missing: list[str]


class MacroTile(BaseModel):
    id: str
    label: str
    group: Literal["index", "volatility", "currency", "yield"]
    symbol: str
    unit: Literal["index", "percent"]
    available: bool
    value: float | None = None
    # Absolute and percent change since the previous daily close.
    change: float | None = None
    change_pct: float | None = None
    # Date of the last daily bar behind the value.
    as_of: str | None = None
    source: str | None = None
    sparkline: list[float] = []


class YieldPoint(BaseModel):
    label: str
    months: int
    value: float | None = None


class YieldCurve(BaseModel):
    points: list[YieldPoint]
    spread_10y_3m: float | None = None
    spread_10y_2y: float | None = None
    # True when long rates sit below short rates; None when it can't be computed.
    inverted: bool | None = None
    note: str | None = None


class MacroResponse(BaseModel):
    as_of: UtcDatetime
    tiles: list[MacroTile]
    yield_curve: YieldCurve
    vix_value: float | None = None
    vix_regime: Literal["elevated", "calm"] | None = None
    vix_threshold: float


class Mover(BaseModel):
    symbol: str
    name: str
    change_pct: float
    price: float


class SectorMove(BaseModel):
    sector: str
    avg_change_pct: float
    count: int


class BreadthStats(BaseModel):
    advancers: int
    decliners: int
    unchanged: int
    new_highs: int
    new_lows: int
    # How many symbols had a full year of history for the high/low tallies.
    high_low_judged: int
    total: int


class RecapResponse(BaseModel):
    as_of: UtcDatetime
    sampled: int
    universe_size: int
    breadth: BreadthStats
    top_gainers: list[Mover]
    top_losers: list[Mover]
    sector_leaders: list[SectorMove]
    sector_laggards: list[SectorMove]
    etf_leaders: list[SectorEtfTile]
    etf_laggards: list[SectorEtfTile]
    vix_value: float | None = None
    vix_change_pct: float | None = None
    vix_regime: Literal["elevated", "calm"] | None = None
    # Rule-based text built from the numbers above.
    summary: str
    # Present only when asked for and an LLM is configured; always AI-written.
    ai_paragraph: str | None = None
    ai_provider: str | None = None
    missing: list[str]
