"""Point-in-time context and the time rules for every dated fact (plan.md F-4).

Idea from quant-mind (LLMQuant/quant-mind, MIT): its knowledge records carry an
information time (`as_of`) and, separately, the time the source became
observable (`available_at`), and its docs warn that only the second one
prevents look-ahead. No code was copied; the names here are our own
(`known_at` is their `available_at`, `effective_at` is their `as_of`).

What "as of" means here
-----------------------
Every reader of dated facts asks "what was public at moment T?". Live, T is
now. A backtest sets T to the simulated moment with

    with as_of(simulated_time):
        ...  # every default read inside sees only facts known by then

`current_as_of()` returns the active simulated moment, or the real current
time when none is set. The context is a ContextVar, so it is per-thread and
per-asyncio-task: `asyncio.to_thread` and anyio/FastAPI's threadpool copy it,
but a bare `ThreadPoolExecutor.submit` / `loop.run_in_executor` does NOT. A
backtester that fans work out to threads must pass
`contextvars.copy_context().run` (or call `as_of` again inside the worker).

A nested `as_of` may only move time *backwards* (or stay put). Asking for a
later moment inside a simulated one raises LookAheadError: that would let code
running "on 2024-03-01" read facts from after 2024-03-01, which is exactly the
bug this layer exists to make impossible.

Time rules (every stored time follows these)
--------------------------------------------
1. **All stored times are naive UTC**, the same convention as the rest of the
   database (`timeutil.utcnow_naive`). A naive datetime handed to this package
   is read as UTC. Anything else must be converted first with `to_naive_utc`
   (tz-aware input) or `source_time_to_utc` (a naive *local* time from a
   source, plus that source's zone).
2. **Convert a source's local time with its zone, never by hand.** Reading a
   New York time as if it were UTC makes it 4-5 hours too EARLY, and "too
   early" is the dangerous direction: it lets a backtest act on news before it
   existed. Nothing downstream can detect that mistake, so it has to be
   prevented at the point of conversion. Known sources, checked live
   2026-09-28 against Apple's Form 4 accession 0001140361-26-037584:
   - SEC submissions JSON (`data.sec.gov/submissions/CIK*.json`),
     `acceptanceDateTime` = "2026-09-24T22:30:07.000Z": genuine UTC, the `Z`
     is honest. Parse it as tz-aware and pass it through `to_naive_utc`.
   - The same filing's EDGAR index page shows "Accepted 2026-09-24 18:30:07":
     that is US/Eastern local time (EDT, UTC-4). Use
     `source_time_to_utc(dt, SEC_EDGAR_TZ)`.
   - `filingDate` alone (a date, no time) is NOT an acceptance time: see rule 5.
3. **known_at is the moment a live trader could first have acted on the
   information**: the source's own publish / acceptance time when it gives one
   (`known_at_basis="source"`).
4. **No source time → known_at is our fetch time** (`known_at_basis="fetched"`).
   We provably had it then; claiming anything earlier would be a guess.
5. **Never set known_at earlier than the source proves.** A date-only source
   (FINRA's daily file, a House filing date) proves only "some time that day",
   so use the END of that local day (`end_of_local_day_utc`,
   `known_at_basis="derived"`), or a later documented publication rule, never
   midnight at its start. Ambiguous / nonexistent local times around a DST
   switch resolve to the LATER of the two possible instants for the same reason.
6. **known_at is never after fetched_at**: we had the fact when we fetched it.
   `record_fact` clamps a later source time to the fetch time (and logs it,
   since a big gap usually means a time-zone bug in the caller).
7. **fetched_at is always the real wall clock**, even inside a simulated
   `as_of`: it records when *this program* got the data, not a simulated time.
8. **Readers include a fact known exactly at the cutoff** (`known_at <= as_of`).
   Windows' clock can return the same value for two quick calls, and a strict
   `<` would then hide a fact recorded and read in the same instant.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.timeutil import utcnow_naive

# EDGAR's human-facing pages (filing index "Accepted", full-text search, the
# daily form index) show acceptance times in New York local time. The
# submissions JSON's `acceptanceDateTime` is already UTC; see rule 2 above.
SEC_EDGAR_TZ = ZoneInfo("America/New_York")
# The default zone for US date-only sources (FINRA's daily file, House filing
# dates). Not verified per source here: each source's own item confirms which
# zone its times are in before relying on this.
US_EASTERN_TZ = SEC_EDGAR_TZ

# A source whose publish time is later than our own fetch time is either a
# small clock difference (harmless; clamped silently below this) or a
# time-zone bug in the caller (clamped and logged above it).
KNOWN_AT_CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)

_SIMULATED_AS_OF: ContextVar[datetime | None] = ContextVar("strategeia_simulated_as_of", default=None)


class LookAheadError(RuntimeError):
    """Raised when code asks to see facts from after the active simulated moment."""


def to_naive_utc(value: datetime) -> datetime:
    """Normalise a datetime to the stored convention (naive UTC).

    tz-aware → converted to UTC; naive → assumed to already be UTC (the app-wide
    convention). A bare `date` is refused: which moment of the day it means is
    a per-source decision (see `end_of_local_day_utc`), not something to guess.
    """
    if not isinstance(value, datetime):
        raise TypeError(
            f"expected a datetime, got {type(value).__name__}; for a date-only source use end_of_local_day_utc()"
        )
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def source_time_to_utc(local_value: datetime, tz: ZoneInfo | str) -> datetime:
    """A source's naive LOCAL wall-clock time in zone `tz` → naive UTC.

    Around a DST switch a local time can be ambiguous (01:30 happens twice in
    November) or nonexistent (02:30 is skipped in March). Both readings are
    computed and the LATER instant wins, so the stored known_at is never
    earlier than the source can prove (rule 5). A tz-aware input already names
    its instant and is simply converted; `tz` is ignored for it.
    """
    if not isinstance(local_value, datetime):
        raise TypeError(f"expected a datetime, got {type(local_value).__name__}")
    if local_value.tzinfo is not None:
        return to_naive_utc(local_value)
    zone = ZoneInfo(tz) if isinstance(tz, str) else tz
    # Convert each fold to UTC *before* comparing: aware datetimes sharing a
    # tzinfo compare on wall-clock fields and ignore `fold`.
    candidates = (
        local_value.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc).replace(tzinfo=None) for fold in (0, 1)
    )
    return max(candidates)


def end_of_local_day_utc(day: date, tz: ZoneInfo | str = US_EASTERN_TZ) -> datetime:
    """The last microsecond of `day` in zone `tz`, as naive UTC.

    The conservative known_at for a source that dates something but doesn't
    time it: public "at some point that day" can only safely be read as public
    by the day's end. Callers with a documented, earlier publication rule
    (e.g. "FINRA posts the file by 6 pm ET") may use that instead, and should
    say so in a comment.
    """
    if isinstance(day, datetime):
        day = day.date()
    return source_time_to_utc(datetime.combine(day, time.max), tz)


def current_as_of() -> datetime:
    """The moment readers look from: the simulated time if one is active, else now (naive UTC)."""
    simulated = _SIMULATED_AS_OF.get()
    return simulated if simulated is not None else utcnow_naive()


def simulated_as_of() -> datetime | None:
    """The active simulated moment, or None when running live."""
    return _SIMULATED_AS_OF.get()


def is_simulated() -> bool:
    return _SIMULATED_AS_OF.get() is not None


@contextmanager
def as_of(moment: datetime) -> Iterator[datetime]:
    """Run the block as if the current time were `moment`.

    Every fact reader that uses its default cutoff sees only facts with
    `known_at <= moment`. Nesting may narrow the window (an earlier moment)
    but never widen it: a later moment inside a simulated one raises
    LookAheadError. On exit the previous value is restored, even on error.
    """
    moment = to_naive_utc(moment)
    outer = _SIMULATED_AS_OF.get()
    if outer is not None and moment > outer:
        raise LookAheadError(
            f"as_of({moment.isoformat()}) inside a block simulating {outer.isoformat()} would see the future"
        )
    token = _SIMULATED_AS_OF.set(moment)
    try:
        yield moment
    finally:
        _SIMULATED_AS_OF.reset(token)
