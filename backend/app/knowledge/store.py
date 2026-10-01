"""Writing and reading dated facts (plan.md F-4).

Writers call `record_fact`. Readers call `facts_known_as_of` / `latest_known`,
which ALWAYS filter `known_at <= cutoff`; the cutoff defaults to
`current_as_of()` (now live, the simulated moment in a backtest). There is no
reader here that skips the filter unless the caller writes
`include_future=True`, which exists for admin/debug views only and must never
appear on a path that feeds a trading decision or a backtest.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.knowledge import point_in_time as pit
from app.knowledge.models import KNOWN_AT_BASES, KnownFact
from app.knowledge.point_in_time import LookAheadError
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

# Lowercase slug: keeps kinds greppable and stops "News"/"news " becoming
# separate streams that a reader of "news" silently misses.
_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# Keys longer than this are hashed by `make_dedupe_key` (a news headline plus
# URL can run to hundreds of characters; the unique index doesn't need them).
MAX_DEDUPE_KEY_LENGTH = 200


@dataclass(frozen=True)
class RecordedFact:
    """What `record_fact` did. Watchers alert only when `created` is True."""

    fact: KnownFact
    created: bool
    known_at_moved_earlier: bool = False


def make_dedupe_key(*parts: object) -> str:
    """A stable identity for a fact from its identifying parts, e.g.
    `make_dedupe_key("sec", accession, line_no)` or `make_dedupe_key(url)`.

    Parts are joined with "|"; a key longer than MAX_DEDUPE_KEY_LENGTH becomes
    "sha256:<hex>" of the same string. None parts are kept as "" so positions
    stay meaningful.
    """
    if not parts:
        raise ValueError("make_dedupe_key needs at least one part")
    joined = "|".join("" if p is None else str(p).strip() for p in parts)
    if len(joined) <= MAX_DEDUPE_KEY_LENGTH:
        return joined
    return "sha256:" + hashlib.sha256(joined.encode("utf-8")).hexdigest()


def payload_fingerprint(payload: Mapping[str, Any]) -> str:
    """sha256 of the payload's canonical JSON (sorted keys).

    For snapshot-type kinds (fundamentals, holdings) put this in the dedupe
    key, so an unchanged re-fetch dedupes and a CHANGED snapshot becomes a new
    fact with its own, later known_at. Reusing one key for a changed snapshot
    would be look-ahead: the new values would appear under the old known_at.
    """
    canonical = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalise_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    cleaned = symbol.strip().upper()
    return cleaned or None


def _check_kind(kind: str) -> str:
    if not isinstance(kind, str) or not _KIND_PATTERN.match(kind):
        raise ValueError(f"invalid fact kind {kind!r}: use a lowercase slug such as 'insider_trade'")
    return kind


def _json_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate the payload is JSON-storable NOW (a clear error at the call
    site beats an opaque one at commit) and copy it so later mutation of the
    caller's dict can't alter what was stored."""
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise TypeError(f"payload must be a mapping, got {type(payload).__name__}")
    try:
        return json.loads(json.dumps(dict(payload)))
    except TypeError as exc:
        raise TypeError(
            f"payload is not JSON-serialisable ({exc}); convert datetimes with .isoformat() and Decimals to float"
        ) from exc


def _find(session: Session, kind: str, dedupe_key: str) -> KnownFact | None:
    return session.exec(select(KnownFact).where(KnownFact.kind == kind, KnownFact.dedupe_key == dedupe_key)).first()


def record_fact(
    session: Session,
    *,
    kind: str,
    source: str,
    dedupe_key: str,
    payload: Mapping[str, Any] | None = None,
    symbol: str | None = None,
    known_at: datetime | None = None,
    known_at_basis: str | None = None,
    effective_at: datetime | None = None,
    source_ref: str | None = None,
    fetched_at: datetime | None = None,
    commit: bool = True,
) -> RecordedFact:
    """Store one fact, idempotently. Safe to call on every fetch.

    - `known_at`: the source's own publish/acceptance time, as naive UTC or
      tz-aware (convert source-local times with `source_time_to_utc` first).
      Omit it when the source gives none: known_at becomes the fetch time.
    - `known_at_basis`: defaults to "source" when known_at is given, else
      "fetched". Pass "derived" when known_at comes from a conservative rule
      (e.g. `end_of_local_day_utc` for a date-only source).
    - `fetched_at`: defaults to the real current time (never the simulated
      as_of). A replay of stored raw files may pass their original fetch time.
    - A known_at later than fetched_at is clamped to fetched_at (we had it
      then) and logged when the gap exceeds the clock-skew tolerance.

    Duplicate (same kind + dedupe_key): nothing is overwritten, except that an
    EARLIER known_at replaces a later one (with its basis). known_at never
    moves later, so a fact a backtest could see stays visible. A different
    payload under an existing key is ignored and logged: a revision must get
    its own key (see `payload_fingerprint`) so it gets its own known_at.

    With `commit=True` (default) the row is committed and refreshed. With
    `commit=False` it is only flushed; the caller commits. On a concurrent
    insert of the same key the session is ROLLED BACK before re-reading the
    winner's row, so don't mix unrelated uncommitted work into the session
    you pass here.
    """
    kind = _check_kind(kind)
    if not source or not source.strip():
        raise ValueError("source is required (e.g. 'sec_edgar', 'yfinance')")
    if not dedupe_key or not dedupe_key.strip():
        raise ValueError("dedupe_key is required; build one with make_dedupe_key()")
    dedupe_key = dedupe_key.strip()
    stored_payload = _json_copy(payload)

    fetched = pit.to_naive_utc(fetched_at) if fetched_at is not None else utcnow_naive()
    if known_at is None:
        resolved_known_at = fetched
        basis = known_at_basis or "fetched"
    else:
        resolved_known_at = pit.to_naive_utc(known_at)
        basis = known_at_basis or "source"
    if basis not in KNOWN_AT_BASES:
        raise ValueError(f"known_at_basis must be one of {sorted(KNOWN_AT_BASES)}, got {basis!r}")
    if resolved_known_at > fetched:
        skew = resolved_known_at - fetched
        if skew > pit.KNOWN_AT_CLOCK_SKEW_TOLERANCE:
            logger.warning(
                "known_at %s for %s/%s is %s after its fetch time; clamped to the fetch time. "
                "A gap this size usually means a source-local time was read as UTC (or vice versa).",
                resolved_known_at.isoformat(), kind, dedupe_key, skew,
            )
        resolved_known_at = fetched

    existing = _find(session, kind, dedupe_key)
    if existing is None:
        fact = KnownFact(
            kind=kind,
            symbol=_normalise_symbol(symbol),
            known_at=resolved_known_at,
            effective_at=pit.to_naive_utc(effective_at) if effective_at is not None else None,
            fetched_at=fetched,
            known_at_basis=basis,
            source=source.strip(),
            source_ref=source_ref,
            dedupe_key=dedupe_key,
            payload=stored_payload,
        )
        session.add(fact)
        try:
            if commit:
                session.commit()
                session.refresh(fact)
            else:
                session.flush()
            return RecordedFact(fact=fact, created=True)
        except IntegrityError:
            # Another writer inserted the same key between our read and write.
            session.rollback()
            existing = _find(session, kind, dedupe_key)
            if existing is None:
                raise

    return _merge_duplicate(session, existing, resolved_known_at, basis, stored_payload, commit)


def _merge_duplicate(
    session: Session,
    existing: KnownFact,
    known_at: datetime,
    basis: str,
    payload: dict[str, Any],
    commit: bool,
) -> RecordedFact:
    if payload and existing.payload != payload:
        logger.warning(
            "fact %s/%s re-recorded with a different payload; kept the original. "
            "Revisions need their own dedupe_key so they get their own known_at.",
            existing.kind, existing.dedupe_key,
        )
    if known_at >= existing.known_at:
        return RecordedFact(fact=existing, created=False)

    logger.info(
        "fact %s/%s: known_at moved earlier %s -> %s (%s)",
        existing.kind, existing.dedupe_key, existing.known_at.isoformat(), known_at.isoformat(), basis,
    )
    existing.known_at = known_at
    existing.known_at_basis = basis
    session.add(existing)
    if commit:
        session.commit()
        session.refresh(existing)
    else:
        session.flush()
    return RecordedFact(fact=existing, created=False, known_at_moved_earlier=True)


def _resolve_cutoff(as_of: datetime | None, include_future: bool) -> datetime | None:
    """The known_at cutoff a read must apply, or None only for include_future."""
    if include_future:
        return None
    simulated = pit.simulated_as_of()
    if as_of is None:
        return simulated if simulated is not None else utcnow_naive()
    cutoff = pit.to_naive_utc(as_of)
    if simulated is not None:
        if cutoff > simulated:
            raise LookAheadError(
                f"read with as_of={cutoff.isoformat()} inside a block simulating {simulated.isoformat()} "
                "would see the future"
            )
        return cutoff
    # Live: nothing can be known later than now, so a future cutoff means now.
    return min(cutoff, utcnow_naive())


def _kinds(kind: str | Iterable[str]) -> list[str]:
    kinds = [kind] if isinstance(kind, str) else list(kind)
    if not kinds:
        raise ValueError("at least one kind is required")
    return [_check_kind(k) for k in kinds]


def facts_known_as_of(
    session: Session,
    kind: str | Iterable[str],
    as_of: datetime | None = None,
    symbol: str | None = None,
    since: datetime | None = None,
    limit: int | None = None,
    *,
    include_future: bool = False,
) -> list[KnownFact]:
    """Facts of `kind` that were public at the cutoff, newest known_at first.

    LOOK-AHEAD GUARD: every result satisfies `known_at <= cutoff`, where the
    cutoff is `as_of` if given, else `current_as_of()` (now live; the simulated
    moment inside `with as_of(...)`). An explicit `as_of` later than an active
    simulated moment raises LookAheadError instead of quietly widening the
    window. `include_future=True` removes the guard entirely; it is for
    admin/debug views ONLY and must never be used by scoring, scans,
    watchers' decisions or the backtester.

    - `symbol`: None = every symbol (and market-wide facts); otherwise matched
      case-insensitively.
    - `since`: only facts with `known_at >= since` (e.g. "the last 90 days").
    - `limit`: newest N after filtering.
    """
    cutoff = _resolve_cutoff(as_of, include_future)
    query = select(KnownFact).where(col(KnownFact.kind).in_(_kinds(kind)))
    if cutoff is not None:
        query = query.where(KnownFact.known_at <= cutoff)
    normalised = _normalise_symbol(symbol)
    if normalised is not None:
        query = query.where(KnownFact.symbol == normalised)
    if since is not None:
        query = query.where(KnownFact.known_at >= pit.to_naive_utc(since))
    query = query.order_by(col(KnownFact.known_at).desc(), col(KnownFact.id).desc())
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        query = query.limit(limit)
    return list(session.exec(query).all())


def latest_known(
    session: Session,
    kind: str | Iterable[str],
    symbol: str | None = None,
    as_of: datetime | None = None,
    *,
    include_future: bool = False,
) -> KnownFact | None:
    """The most recently published fact of `kind` at the cutoff, or None.
    Same look-ahead guard and `include_future` warning as `facts_known_as_of`."""
    found = facts_known_as_of(session, kind, as_of=as_of, symbol=symbol, limit=1, include_future=include_future)
    return found[0] if found else None
