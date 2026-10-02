"""The stored committee run: every agent report, in order, and the final rating.

One row per run. The reports are a JSON list because they are written as the run goes (so a page
that polls sees each report appear) and always read whole. Times are naive UTC.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.timeutil import utcnow_naive

RUN_QUEUED = "queued"
RUN_RUNNING = "running"
RUN_DONE = "done"
RUN_FAILED = "failed"
ACTIVE_RUN_STATUSES = (RUN_QUEUED, RUN_RUNNING)

# The five ratings, most bullish first.
RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")


class CommitteeRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    status: str = Field(default=RUN_QUEUED, index=True)
    created_at: datetime = Field(default_factory=utcnow_naive, index=True)
    finished_at: Optional[datetime] = None

    # JSON list of steps: {key, role, title, tier, status (pending|running|done|skipped|failed),
    # text, error, model, started_at, finished_at, ungrounded (list of strings), round}.
    steps: str = "[]"

    rating: Optional[str] = None  # one of RATINGS, set only by a final step that was read
    rating_summary: Optional[str] = None
    rating_key_risks: Optional[str] = None
    rating_conviction: Optional[str] = None  # low | medium | high, as the committee said it
    rating_parse: Optional[str] = None  # structured | lenient | failed

    # The budget: calls made against the cap that applied, and the round caps used.
    llm_calls_used: int = 0
    llm_calls_max: int = 0
    debate_rounds: int = 1
    risk_rounds: int = 1
    provider: Optional[str] = None
    web_search: bool = False  # research mode allowed web search for the analysts of this run
    error: Optional[str] = None
