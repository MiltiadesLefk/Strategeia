from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime


class CommitteeStartRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)


class CommitteeStepSchema(BaseModel):
    key: str
    role: str  # analyst | debate | manager | trader | risk | final
    title: str
    tier: str  # routine | decision: which model tier answers
    round: int = 0
    status: str  # pending | running | done | skipped | failed
    text: str = ""
    error: str | None = None
    note: str | None = None
    model: str | None = None
    sources: list[str] = []
    # Figures the model quoted that are not in the data it was given. A warning only.
    ungrounded: list[str] = []
    started_at: str | None = None
    finished_at: str | None = None


class CommitteeRunSummarySchema(BaseModel):
    id: int
    symbol: str
    status: str
    created_at: UtcDatetime
    finished_at: UtcDatetime | None
    rating: str | None
    llm_calls_used: int
    llm_calls_max: int


class CommitteeRunSchema(CommitteeRunSummarySchema):
    steps: list[CommitteeStepSchema]
    rating_summary: str | None
    rating_key_risks: str | None
    rating_conviction: str | None
    rating_parse: str | None
    debate_rounds: int
    risk_rounds: int
    provider: str | None
    web_search: bool
    error: str | None
    # Plain-words reminder shown with the rating: an opinion only.
    note: str


class CommitteeRunList(BaseModel):
    runs: list[CommitteeRunSummarySchema]
