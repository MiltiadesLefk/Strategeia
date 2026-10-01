from __future__ import annotations

from pydantic import BaseModel


class EstimateSchema(BaseModel):
    fiscal_period_label: str | None = None
    eps_estimate: float | None = None
    revenue_estimate: float | None = None


class ReactionSchema(BaseModel):
    report_date: str
    move_pct: float


class ScenarioSchema(BaseModel):
    name: str  # Bull | Base | Bear
    percentile: int
    move_pct: float
    description: str


class ImpliedMoveSchema(BaseModel):
    expiration: str
    implied_move_pct: float
    # True when the options expiration falls on or after the report date, i.e. the
    # price of those options actually includes the earnings event.
    covers_earnings: bool
    historical_median_move_pct: float | None = None
    ratio: float | None = None  # only set when covers_earnings
    verdict: str | None = None  # rich | cheap | in line


class SurpriseRowSchema(BaseModel):
    report_date: str
    eps_estimate: float | None
    eps_actual: float | None
    surprise_pct: float | None
    reaction_pct: float | None  # the stock's move around that report, when the price history reaches it


class TrackRecordSchema(BaseModel):
    quarters: int
    beats: int
    misses: int
    in_line: int
    average_surprise_pct: float | None = None


class PriceContextSchema(BaseModel):
    price: float
    trend: str
    momentum: str
    rsi14: float
    pct_from_ema20: float
    week52_low: float | None = None
    week52_high: float | None = None


class EarningsPreviewResponse(BaseModel):
    symbol: str
    name: str | None = None
    earnings_date: str | None = None
    days_until: int | None = None
    estimate: EstimateSchema | None = None
    price_context: PriceContextSchema | None = None
    historical_move_pct: float | None = None  # median absolute move on past reports
    reactions_sampled: int = 0
    implied_move: ImpliedMoveSchema | None = None
    track_record: TrackRecordSchema | None = None
    surprise_table: list[SurpriseRowSchema] = []
    scenarios: list[ScenarioSchema] = []
    scenarios_note: str | None = None  # why the table is empty, when it is
    what_to_watch: list[str] = []
    data_gaps: list[str] = []  # sections that could not be built, in plain words
    summary: str
    summary_provider: str  # "none" = rule-based text, otherwise the AI provider
    summary_error: str | None = None
    generated_at: str
