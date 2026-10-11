from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class CandidateComponentOut(BaseModel):
    source: str  # "insider" | "house" | "ownership" | "fund" | "filing_8k"
    points: float
    reason: str
    delay_note: str | None  # how late this kind of report can be ("reported", never "happened")


class SmartMoneyCandidateOut(BaseModel):
    id: int
    symbol: str
    score: float
    components: list[CandidateComponentOut]
    status: str
    reason: str | None
    scope: str | None
    scored_at: UtcDatetime  # "as of": the moment the reported filings were counted
    first_trigger_known_at: UtcDatetime | None  # when the earliest scoring filing became public
    created_at: UtcDatetime
    expires_at: UtcDatetime | None
    plan_id: int | None
    sleeve_key: str | None
    decided_by: str | None
    decided_at: UtcDatetime | None
    notes: str | None


class SmartMoneyCandidatesResponse(BaseModel):
    scope: str  # the tracker scope currently set: "universe" | "market_wide"
    min_score: float
    total: int
    candidates: list[SmartMoneyCandidateOut]
    # Always shown with the list: what these numbers are and are not.
    disclaimer: str
