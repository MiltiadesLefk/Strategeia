"""The point-in-time fact table (plan.md F-4). See app/knowledge/__init__.py.

One generic table rather than one per source, on purpose: the dozen planned
fact sources (news, fundamentals snapshots, Form 4s, 8-Ks, FINRA files, Fed
speeches, posts, Congress reports, 13F holdings, ...) differ in their payload
but share exactly the columns the look-ahead guard needs (kind, symbol,
known_at). Keeping those columns in one place means there is one reader, one
guard and one set of tests, instead of each source re-implementing
`known_at <= as_of` and one of them eventually forgetting it. Source-specific
fields live in `payload` (JSON). If a kind later needs SQL-side filtering on a
payload field (e.g. insider trades by transaction code over years of history),
add a typed side table keyed by `knownfact.id` rather than widening this one;
see notes/Decisions.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from sqlalchemy import JSON, Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.timeutil import utcnow_naive

KnownAtBasis = Literal["source", "fetched", "derived"]
KNOWN_AT_BASES: frozenset[str] = frozenset({"source", "fetched", "derived"})


class FactKind:
    """Shared spellings for `KnownFact.kind`, so two sources of the same thing
    don't end up in two streams. New kinds are fine (any lowercase slug is
    accepted); add them here when a second module needs the name."""

    NEWS = "news"  # QM-1: a headline/article as we fetched it
    FUNDAMENTALS_SNAPSHOT = "fundamentals_snapshot"  # QM-1: one fetch of overview/financials
    INSIDER_TRADE = "insider_trade"  # SG-1 / WA-2: one Form 4 transaction, known at SEC acceptance
    SEC_FILING_8K = "sec_filing_8k"  # SG-6: an 8-K with its item codes
    OWNERSHIP_FILING = "ownership_filing"  # SM-5: Schedule 13D/13G
    FINRA_SHORT_VOLUME = "finra_short_volume"  # SG-5: one symbol's row of a daily file
    FED_SPEECH = "fed_speech"  # SG-7: speech/statement, market-wide (symbol None)
    POST = "post"  # SG-4: a social post, market-wide unless it names a company
    CONGRESS_TRADE = "congress_trade"  # SG-2: known at the REPORT date, trade date is effective_at
    FUND_HOLDING = "fund_holding"  # SG-3 / SM-3: a 13F line, known at filing acceptance
    WATCHER_EVENT = "watcher_event"  # an event a watcher reported (watchers/runner.py); symbol None = market-wide
    FUNDAMENTALS_REVENUE = "fundamentals_revenue"  # one annual revenue value from one 10-K, known at its filing date
    EARNINGS_REPORT = "earnings_report"  # one reported quarter (EPS estimate/actual), known at the end of the report day


class KnownFact(SQLModel, table=True):
    """One piece of information and the moment it became public.

    - `known_at`: when a live trader could first have acted on it (the
      source's publish/acceptance time, else our fetch time). The ONLY column
      the look-ahead guard filters on.
    - `effective_at`: what the fact is *about* (a trade date, a report
      period end). Informational; never a visibility cutoff. A Congress trade
      made in March and reported in April has effective_at=March,
      known_at=April, and a backtest sees it only from April.
    - `fetched_at`: when this program got it (always the real clock).
    - `known_at_basis`: how known_at was established: "source" (the source's
      own timestamp), "fetched" (no source time, so our fetch time), or
      "derived" (a conservative rule, e.g. the end of a date-only filing day).

    Rows are write-once except that `known_at` may move EARLIER when better
    evidence arrives (see store.record_fact). Never write to this table
    directly; go through `app.knowledge.record_fact`.
    """

    __table_args__ = (
        UniqueConstraint("kind", "dedupe_key", name="uq_knownfact_kind_dedupe_key"),
        # The shape of every reader's query: one kind, optionally one symbol,
        # known_at bounded by the cutoff, newest first.
        Index("ix_knownfact_kind_symbol_known_at", "kind", "symbol", "known_at"),
        Index("ix_knownfact_kind_known_at", "kind", "known_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str
    symbol: Optional[str] = None
    known_at: datetime
    effective_at: Optional[datetime] = None
    fetched_at: datetime = Field(default_factory=utcnow_naive)
    known_at_basis: str = "fetched"
    source: str
    source_ref: Optional[str] = None
    dedupe_key: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
