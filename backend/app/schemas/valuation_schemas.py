from __future__ import annotations

from pydantic import BaseModel


class DcfAssumptions(BaseModel):
    """The inputs the projection used. `*_default` says what the data suggested,
    so the screen can show which numbers the user changed."""

    growth_pct: float
    net_margin_pct: float
    discount_rate_pct: float
    terminal_growth_pct: float
    years: int
    growth_default_pct: float | None = None
    margin_default_pct: float | None = None
    growth_source: str = ""
    margin_source: str = ""


class DcfYear(BaseModel):
    year: int
    revenue: float
    earnings: float
    present_value: float


class SensitivityCell(BaseModel):
    discount_rate_pct: float
    terminal_growth_pct: float
    value_per_share: float | None = None


class DcfResult(BaseModel):
    assumptions: DcfAssumptions
    projection: list[DcfYear]
    terminal_value: float
    terminal_present_value: float
    equity_value: float
    shares: float | None = None
    value_per_share: float | None = None
    price: float | None = None
    upside_pct: float | None = None
    terminal_share_pct: float
    sensitivity: list[SensitivityCell] = []


class PeerRow(BaseModel):
    symbol: str
    name: str
    market_cap: float | None = None
    pe_ratio: float | None = None
    price_to_sales: float | None = None


class MultipleSummary(BaseModel):
    """One multiple across the peers. `count` is how many peers had a usable
    value; `implied_price` applies the median to the subject's own figure."""

    name: str
    subject: float | None = None
    median: float | None = None
    low: float | None = None
    high: float | None = None
    count: int = 0
    implied_price: float | None = None


class CompsResult(BaseModel):
    sector: str | None = None
    peers: list[PeerRow] = []
    multiples: list[MultipleSummary] = []
    reason: str | None = None


class ValuationResponse(BaseModel):
    symbol: str
    name: str
    available: bool
    reason: str | None = None
    price: float | None = None
    dcf: DcfResult | None = None
    comps: CompsResult | None = None
    notes: list[str] = []
    summary: str | None = None
    summary_source: str | None = None
