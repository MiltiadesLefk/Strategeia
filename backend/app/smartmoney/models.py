"""The smart-money candidate table (NEW_PLAN section 6, step 3).

One row per time a symbol's smart-money score crossed the bar (and the re-flag
guard allowed a new flag). A candidate only says "this symbol deserves an
ordinary full evaluation"; it never carries a direction, entry, stop or size.
In this phase (tracker only) a candidate stays `new`; later phases move it
through evaluation, the review queue and the sleeves.

Times are naive UTC like every other column.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.timeutil import utcnow_naive

# Lifecycle. Phase 1 only ever writes STATUS_NEW (and STATUS_EXPIRED when a stale one is closed).
STATUS_NEW = "new"  # flagged, not evaluated yet
STATUS_EVALUATING = "evaluating"
STATUS_AUTO_BOUGHT = "auto_bought"
STATUS_NEEDS_REVIEW = "needs_review"  # rated bad: the user decides
STATUS_BOUGHT = "bought"  # the user bought from the review queue
STATUS_SKIPPED = "skipped"  # illiquid, no prices, evaluation error, or the user skipped
STATUS_EXPIRED = "expired"
CANDIDATE_STATUSES = (
    STATUS_NEW,
    STATUS_EVALUATING,
    STATUS_AUTO_BOUGHT,
    STATUS_NEEDS_REVIEW,
    STATUS_BOUGHT,
    STATUS_SKIPPED,
    STATUS_EXPIRED,
)


class SmartMoneyCandidate(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    # The score at the moment of the flag, and the parts it was made of:
    # [{"source": ..., "points": ..., "reason": ...}]. Stored so the card can explain itself later.
    score: float
    components: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    # The moment the score was computed (what "now" meant to the scorer) and the
    # earliest known_at among the facts that made it: the earliest a trader could have seen it.
    scored_at: datetime = Field(default_factory=utcnow_naive)
    first_trigger_known_at: Optional[datetime] = None
    status: str = Field(default=STATUS_NEW, index=True)
    # The trade plan an evaluation produced (later phases).
    plan_id: Optional[int] = None
    decided_by: Optional[str] = None  # "auto" | "user"
    decided_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow_naive)
    # Why it is in this status ("illiquid", "no_prices", ...) and an optional user note.
    reason: Optional[str] = None
    reason_detail: Optional[str] = None  # the plain-English detail of `reason` ("last close $2.10 is under $3.00")
    notes: Optional[str] = None
    # The sleeve that bought it (later phases); the tracker itself never opens anything.
    sleeve_key: Optional[str] = None
    # "universe" | "market_wide": the tracker scope that found it.
    scope: Optional[str] = None
    # The position a review-queue Buy opened (the auto sleeve's position is found through plan_id).
    position_id: Optional[int] = None
    # Set while a Buy waits for the market to open (a queued DeferredEvaluation will fill it from fresh data).
    buy_queued_at: Optional[datetime] = None


class SmartMoneyEventAlert(SQLModel, table=True):
    """One Telegram event-exit alert already sent for a held smart-money position (so a poll never repeats it).
    `ref` is the filing's accession number or the earnings date. Alert only: nothing here ever sells."""

    id: Optional[int] = Field(default=None, primary_key=True)
    position_id: int = Field(index=True)
    kind: str  # "negative_8k" | "earnings_miss"
    ref: str
    created_at: datetime = Field(default_factory=utcnow_naive)
