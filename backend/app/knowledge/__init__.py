"""What the app knew, and when (plan.md F-4).

Every piece of data we store, prices aside, is a `KnownFact` stamped with
`known_at`: the moment it became public. Live, readers see everything known up
to now; a backtest wraps its work in `with as_of(simulated_moment):` and every
default read then sees only what was public by that moment. That is what lets a
new signal (insider buys, 8-Ks, FINRA short volume, Congress reports, ...) be
backtested honestly, one at a time, before it earns points (plan.md §3.10).

Writing (every source: QM-1 archive, SG-* signals, WA-* watchers, SM-* feeds):

    from app.knowledge import FactKind, make_dedupe_key, record_fact, source_time_to_utc
    result = record_fact(
        session,
        kind=FactKind.INSIDER_TRADE,
        symbol="AAPL",
        source="sec_edgar",
        source_ref=accession_url,
        dedupe_key=make_dedupe_key(accession_number, line_no),
        known_at=acceptance_utc,          # the source's own time; omit if it has none
        effective_at=transaction_date,    # what it's about; never a visibility cutoff
        payload={"code": "P", "shares": 1000, "price": 180.5},
    )
    if result.created: ...                # a new fact: alert, re-check, ...

Reading (scoring, scans, watchers, the backtester):

    from app.knowledge import as_of, facts_known_as_of, latest_known
    buys = facts_known_as_of(session, FactKind.INSIDER_TRADE, symbol="AAPL", since=cutoff_90d)
    with as_of(datetime(2024, 3, 1, 21, 0)):      # backtest: naive UTC
        buys = facts_known_as_of(session, FactKind.INSIDER_TRADE, symbol="AAPL")  # nothing after it

The time rules (naive UTC, converting source-local times, fetch time when a
source has none, never earlier than the source proves) are in
`point_in_time.py`'s docstring. Congress trades: test on the REPORT date
(known_at), never the trade date (effective_at).
"""

from app.knowledge.models import KNOWN_AT_BASES, FactKind, KnownAtBasis, KnownFact
from app.knowledge.point_in_time import (
    KNOWN_AT_CLOCK_SKEW_TOLERANCE,
    SEC_EDGAR_TZ,
    US_EASTERN_TZ,
    LookAheadError,
    as_of,
    current_as_of,
    end_of_local_day_utc,
    is_simulated,
    simulated_as_of,
    source_time_to_utc,
    to_naive_utc,
)
from app.knowledge.store import (
    MAX_DEDUPE_KEY_LENGTH,
    RecordedFact,
    facts_known_as_of,
    latest_known,
    make_dedupe_key,
    payload_fingerprint,
    record_fact,
)

__all__ = [
    "KNOWN_AT_BASES",
    "KNOWN_AT_CLOCK_SKEW_TOLERANCE",
    "MAX_DEDUPE_KEY_LENGTH",
    "SEC_EDGAR_TZ",
    "US_EASTERN_TZ",
    "FactKind",
    "KnownAtBasis",
    "KnownFact",
    "LookAheadError",
    "RecordedFact",
    "as_of",
    "current_as_of",
    "end_of_local_day_utc",
    "facts_known_as_of",
    "is_simulated",
    "latest_known",
    "make_dedupe_key",
    "payload_fingerprint",
    "record_fact",
    "simulated_as_of",
    "source_time_to_utc",
    "to_naive_utc",
]
