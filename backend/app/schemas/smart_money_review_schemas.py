from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime


class ReviewComponentOut(BaseModel):
    source: str  # "insider" | "house" | "ownership" | "fund" | "filing_8k"
    points: float
    reason: str
    delay_note: str | None = None  # how late this kind of report can be ("reported", never "happened")


class ReviewRatingOut(BaseModel):
    confidence_score: int
    confidence_points: int | None
    confidence_points_max: int | None
    verdict_text: str


class ReviewPlanOut(BaseModel):
    """What the full evaluation wrote (a summary; the plan itself is on the Trade Plans page)."""

    status: str
    direction: str | None
    entry: float | None
    stop: float | None
    tp1: float | None
    rr1: float | None
    suggested_shares: int | None
    auto_execute_note: str | None
    created_at: UtcDatetime


class ReviewItemOut(BaseModel):
    id: int
    symbol: str
    status: str  # needs_review | bought | skipped | expired | auto_bought (| new | evaluating while still in the pipeline)
    score: float
    components: list[ReviewComponentOut]
    plan_id: int | None
    rating: ReviewRatingOut | None
    reasons_declined: list[str]
    expires_at: UtcDatetime | None
    created_at: UtcDatetime
    decided_by: str | None
    decided_at: UtcDatetime | None
    note: str | None
    delay_note: str
    market_open: bool
    # Additive: not part of the frontend contract above.
    plan: ReviewPlanOut | None = None
    sleeve_key: str | None = None
    position_id: int | None = None
    reason: str | None = None
    buy_queued: bool = False
    scored_at: UtcDatetime | None = None


class HindsightGroupOut(BaseModel):
    key: str  # "user_skipped" | "expired" | "undecided"
    label: str
    n: int
    resolved: int
    open: int
    awaiting: int  # no result computed yet (the missed-trades refresh has not reached it)
    not_simulated: int
    wins: int
    win_rate: float | None
    avg_r: float | None
    avg_r_low: float | None
    avg_r_high: float | None
    small_sample: bool


class HindsightOut(BaseModel):
    groups: list[HindsightGroupOut]
    min_trades_for_reading: int
    note: str


class ReviewListResponse(BaseModel):
    items: list[ReviewItemOut]
    disclaimer: str
    hindsight: HindsightOut | None = None


class ReviewBuyResponse(ReviewItemOut):
    position_id: int | None = None  # None while the Buy waits for the market to open


class ReviewSkipRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class SleevesOut(BaseModel):
    auto_key: str
    manual_key: str
    created: bool
