"""FINRA short-sale volume as dated facts, and a reader that respects the clock.

Writing: `ingest_finra_short_volume` stores one fact per (symbol, trading day)
for the symbols you ask about only (the watchlist), never the whole market.

When did a day's file become public? FINRA's files carry a Last-Modified time;
the files for 2026-09-29 and 2026-09-30 were both stamped about 17:18 US/Eastern
on their own trading day, and FINRA describes the files as posted the evening
of the trading day. Two samples are not a guarantee, so the rule here is the
conservative one: a day's file counts as public at the END of that trading day
in US/Eastern (`known_at` basis "derived"), a few hours later than observed.
A backtest standing at 4 pm on the trading day therefore cannot see that day's
volume, which a live trader could not either.

Reading: `short_volume_ratio_as_of` goes through `facts_known_as_of`, so it only
sees files that were public at the cutoff (now live, the simulated moment
inside `with as_of(...)`).

Short-sale volume is not short interest. A large share of it is market makers
hedging and providing liquidity, so the ratio is context, never a verdict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median

from sqlmodel import Session, col, select

from app.data_providers.base import DataProviderError
from app.data_providers.finra_provider import FinraProvider, weekdays_between
from app.knowledge import (
    FactKind,
    KnownFact,
    current_as_of,
    end_of_local_day_utc,
    facts_known_as_of,
    make_dedupe_key,
    record_fact,
)
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

SOURCE = "finra_regsho"
# How many of the most recent trading days make up "the recent ratio" (volume-weighted).
RECENT_LOOKBACK_DAYS = 5
# The baseline is the median of up to this many daily ratios before the recent window,
# and is only used once at least MIN_BASELINE_DAYS of them exist.
BASELINE_DAYS = 60
MIN_BASELINE_DAYS = 20
# A reading whose newest trading day is older than this (calendar days before the
# cutoff) is stale: it would describe a different market, so it is not used.
MAX_READING_AGE_DAYS = 7
# Manual refresh: how far back to look for missing days.
REFRESH_LOOKBACK_DAYS = 10


@dataclass(frozen=True)
class IngestResult:
    days_checked: int = 0
    days_fetched: int = 0
    days_without_file: int = 0
    facts_created: int = 0
    facts_existing: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ShortVolumeReading:
    """Recent short-volume ratio for one symbol, as known at a cutoff."""

    symbol: str
    recent_ratio: float  # volume-weighted over the newest `recent_days` trading days
    recent_days: int
    baseline_ratio: float | None  # median daily ratio before that window; None when too little data
    baseline_days: int
    latest_trade_date: date


def _fact_dedupe_key(symbol: str, day: date) -> str:
    return make_dedupe_key("finra", symbol.upper(), day.strftime("%Y%m%d"))


def _clean_symbols(symbols: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for s in symbols:
        cleaned = (s or "").strip().upper()
        if cleaned:
            seen.setdefault(cleaned, None)
    return list(seen)


def _existing_keys(session: Session, symbols: list[str], start: date, end: date) -> set[str]:
    # Filtered by symbol and trading-day range in SQL (a long backfill of many
    # symbols would otherwise need a huge key list), keys compared in Python.
    found = session.exec(
        select(KnownFact.dedupe_key).where(
            KnownFact.kind == FactKind.FINRA_SHORT_VOLUME,
            col(KnownFact.symbol).in_(symbols),
            KnownFact.effective_at >= datetime(start.year, start.month, start.day),
            KnownFact.effective_at < datetime(end.year, end.month, end.day) + timedelta(days=1),
        )
    ).all()
    return set(found)


def ingest_finra_short_volume(
    session: Session,
    symbols: list[str],
    start: date,
    end: date,
    provider: FinraProvider | None = None,
) -> IngestResult:
    """Store FINRA short volume for `symbols` on every trading day in [start, end].

    Idempotent and resumable: a day whose every requested symbol is already
    stored is skipped without any download, so an interrupted backfill simply
    continues. A day FINRA has no file for (holiday, not posted yet) is counted
    and skipped. A failed download is recorded in `errors` and the run goes on
    to the next day; nothing is guessed to fill the gap.
    """
    wanted = _clean_symbols(symbols)
    if not wanted or end < start:
        return IngestResult()
    provider = provider or FinraProvider()
    have = _existing_keys(session, wanted, start, end)

    checked = fetched = no_file = created = existing = 0
    errors: list[str] = []
    for day in weekdays_between(start, end):
        checked += 1
        missing = [s for s in wanted if _fact_dedupe_key(s, day) not in have]
        if not missing:
            existing += len(wanted)
            continue
        try:
            rows = provider.get_short_volume(missing, day)
        except DataProviderError as exc:
            errors.append(f"{day.isoformat()}: {exc}")
            logger.warning("FINRA short volume for %s failed: %s", day.isoformat(), exc)
            continue
        if rows is None:
            no_file += 1
            continue
        fetched += 1
        known_at = end_of_local_day_utc(day)
        for symbol, row in rows.items():
            ratio = row.ratio
            if ratio is None:
                continue  # a zero-volume line carries no ratio; not stored as a fake 0
            result = record_fact(
                session,
                kind=FactKind.FINRA_SHORT_VOLUME,
                symbol=symbol,
                source=SOURCE,
                source_ref=provider.url_for(day),
                dedupe_key=_fact_dedupe_key(symbol, day),
                known_at=known_at,
                known_at_basis="derived",
                effective_at=datetime(day.year, day.month, day.day),
                payload={
                    "short_volume": row.short_volume,
                    "short_exempt_volume": row.short_exempt_volume,
                    "total_volume": row.total_volume,
                    "ratio": ratio,
                },
            )
            created += 1 if result.created else 0
            existing += 0 if result.created else 1
    return IngestResult(checked, fetched, no_file, created, existing, errors)


def refresh_finra_short_volume(
    session: Session,
    symbols: list[str],
    provider: FinraProvider | None = None,
    today: date | None = None,
) -> IngestResult:
    """Bring the last REFRESH_LOOKBACK_DAYS of `symbols` up to date. The entry
    point for the manual endpoint and, later, a scheduled watcher."""
    today = today or utcnow_naive().date()
    return ingest_finra_short_volume(session, symbols, today - timedelta(days=REFRESH_LOOKBACK_DAYS), today, provider)


def short_volume_ratio_as_of(
    session: Session,
    symbol: str,
    as_of: datetime | None = None,
    lookback_days: int = RECENT_LOOKBACK_DAYS,
    baseline_days: int = BASELINE_DAYS,
) -> ShortVolumeReading | None:
    """The symbol's recent short-volume ratio and its own baseline, using only
    files public at `as_of` (default: the current moment or simulated moment).
    None when nothing is stored (never a guess)."""
    if lookback_days < 1 or baseline_days < 1:
        raise ValueError("lookback_days and baseline_days must be at least 1")
    facts = facts_known_as_of(
        session, FactKind.FINRA_SHORT_VOLUME, as_of=as_of, symbol=symbol, limit=lookback_days + baseline_days
    )
    # Newest TRADING day first (known_at orders by publication, which is the same order, but be explicit).
    facts = sorted(facts, key=lambda f: f.effective_at or f.known_at, reverse=True)
    facts = [f for f in facts if (f.payload or {}).get("total_volume", 0) > 0]
    if not facts:
        return None
    recent = facts[:lookback_days]
    short_total = sum(float(f.payload["short_volume"]) for f in recent)
    volume_total = sum(float(f.payload["total_volume"]) for f in recent)
    if volume_total <= 0:
        return None
    older_ratios = [float(f.payload["ratio"]) for f in facts[lookback_days:]]
    baseline = median(older_ratios) if len(older_ratios) >= MIN_BASELINE_DAYS else None
    newest = recent[0].effective_at or recent[0].known_at
    return ShortVolumeReading(
        symbol=symbol.strip().upper(),
        recent_ratio=short_total / volume_total,
        recent_days=len(recent),
        baseline_ratio=baseline,
        baseline_days=len(older_ratios),
        latest_trade_date=newest.date(),
    )


def reading_is_fresh(reading: ShortVolumeReading, now: datetime | None = None) -> bool:
    """False when the newest trading day is too old to describe today's market."""
    now = now or current_as_of()
    return (now.date() - reading.latest_trade_date).days <= MAX_READING_AGE_DAYS
