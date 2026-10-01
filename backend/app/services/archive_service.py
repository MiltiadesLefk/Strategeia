"""The dated archive: every news item and fundamentals snapshot we fetch, saved
with the time it became public.

Why this exists: Yahoo (and every free source we use) only shows today's news
and today's fundamentals. There is no way to ask it "what did the headlines say
on 2026-03-12?" afterwards. The only way a future backtest can replay what was
known on a past day is for us to have written it down on that day, so the
archive starts recording now and its value grows with every day it runs. A
backtest can only use archived news from the day the archive began; before
that, news simply is not available to it (and the app says so).

Idea from quant-mind (LLMQuant/quant-mind, MIT): keep the time a record became
observable separate from what it is about. No code was copied. Everything goes
through `app.knowledge.record_fact`, so the same look-ahead guard applies to
every reader here.

What the archive never does:
- It makes no network calls. It stores exactly what the caller already fetched.
- It never breaks the caller. `archive_fetched_data` swallows and logs every
  failure: an archive problem must not fail a research page or a trade
  decision, and nothing here feeds back into a score.
- It never writes inside a simulated `as_of` block. A backtest replays the
  past; letting it write would stamp old data with today's fetch time.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from sqlmodel import Session

from app.data_providers.base import CompanyOverview, FinancialYear, NewsItem
from app.knowledge import (
    FactKind,
    KnownFact,
    fact_stats,
    facts_known_as_of,
    is_simulated,
    make_dedupe_key,
    payload_fingerprint,
    record_fact,
    to_naive_utc,
)
from app.schemas.archive_schemas import (
    ArchivedFundamentalsSchema,
    ArchivedNewsSchema,
    ArchiveResponse,
    ArchiveTotals,
)
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

# `known_at_basis` values this module uses (see knowledge.point_in_time rules 3-4).
BASIS_SOURCE = "source"
BASIS_FETCHED = "fetched"

# How many archived items the reader helpers and the endpoint return when the
# caller gives no limit of its own. News is saved 5 per fetch, so this is a
# few days of coverage for an actively watched symbol.
DEFAULT_RECENT_LIMIT = 20


@dataclass(frozen=True)
class ArchiveWriteResult:
    """What one archive call did: how many items were new vs already saved."""

    new: int = 0
    already_saved: int = 0


# --- time parsing ----------------------------------------------------------------------


def parse_published_at(raw: str | None) -> datetime | None:
    """A news item's own publish time as naive UTC, or None when we can't prove one.

    Only a time that carries its own zone is trusted (an ISO string ending in
    "Z" or "+00:00", or an RFC 2822 date with an offset). A bare "2026-09-01T09:00:00"
    could be New York or London local time, and reading local time as UTC makes
    the item look public hours EARLIER than it was, which is the dangerous
    direction for a backtest. Those fall back to our fetch time instead.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return to_naive_utc(parsed)


# --- writing -----------------------------------------------------------------------------


def _provider_label(data_provider: object | None) -> str:
    name = getattr(data_provider, "name", None)
    return name if isinstance(name, str) and name.strip() else "unknown"


def archive_news(
    session: Session,
    symbol: str,
    items: Iterable[NewsItem],
    source: str,
    *,
    fetched_at: datetime | None = None,
) -> ArchiveWriteResult:
    """Save each news item once. `source` is the data provider that supplied
    them (the publisher, e.g. "Reuters", stays in the payload).

    - known_at: the item's own publish time when it carries a zone (basis
      "source"), else the fetch time (basis "fetched"). Never later than the
      fetch time (record_fact clamps), never invented.
    - Identity: symbol + URL, or symbol + publisher + headline when the item has
      no URL. The same article fetched again (or by a different provider) is the
      same fact; the same article for two symbols is two facts, one per symbol.
    """
    fetched = to_naive_utc(fetched_at) if fetched_at is not None else utcnow_naive()
    new = seen = 0
    for item in items:
        headline = (item.headline or "").strip()
        if not headline:
            continue
        url = (item.url or "").strip()
        publisher = (item.source or "").strip()
        published = parse_published_at(item.published_at)
        key = make_dedupe_key(symbol.upper(), url) if url else make_dedupe_key(symbol.upper(), publisher, headline)
        result = record_fact(
            session,
            kind=FactKind.NEWS,
            symbol=symbol,
            source=source,
            source_ref=url or None,
            dedupe_key=key,
            known_at=published,
            known_at_basis=BASIS_SOURCE if published is not None else BASIS_FETCHED,
            fetched_at=fetched,
            payload={
                "headline": headline,
                "publisher": publisher,
                "url": url,
                # The raw string exactly as the provider gave it, so a bad
                # parse can always be re-done from what was stored.
                "published_at": item.published_at or "",
                "data_provider": source,
            },
        )
        if result.created:
            new += 1
        else:
            seen += 1
    return ArchiveWriteResult(new=new, already_saved=seen)


def _finite(value: Any) -> float | None:
    """A number or None: NaN/inf are not valid JSON and mean "no value" anyway."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _snapshot_payloads(
    overview: CompanyOverview, financial_years: Sequence[FinancialYear], source: str
) -> tuple[dict[str, Any], str]:
    """(payload, fingerprint) for one fetch.

    The fingerprint covers only the slow-moving numbers (trailing revenue and
    EPS, the annual history). Market cap, P/E and the 52-week range move with
    every price tick, so including them would make "changed" mean "the price
    moved" and store a new snapshot on nearly every fetch. They are still kept
    in the payload, as they were when this revision of the fundamentals was
    first seen; P/E and market cap can always be rebuilt from price and EPS.
    """
    years = [
        {"year": int(fy.year), "revenue": _finite(fy.revenue), "net_income": _finite(fy.net_income)}
        for fy in financial_years
    ]
    core = {
        "name": overview.name,
        "revenue_ttm": _finite(overview.revenue_ttm),
        "eps_ttm": _finite(overview.eps_ttm),
        "financial_years": years,
    }
    fingerprint = payload_fingerprint(core)
    payload = {
        **core,
        "market_cap": _finite(overview.market_cap),
        "pe_ratio": _finite(overview.pe_ratio),
        "week52_low": _finite(overview.week52_low),
        "week52_high": _finite(overview.week52_high),
        "latest_fiscal_year": max((y["year"] for y in years), default=None),
        "data_provider": source,
        "fingerprint": fingerprint,
    }
    return payload, fingerprint


def archive_fundamentals(
    session: Session,
    symbol: str,
    overview: CompanyOverview | None,
    financial_years: Sequence[FinancialYear],
    source: str,
    *,
    fetched_at: datetime | None = None,
) -> ArchiveWriteResult:
    """Save one fundamentals snapshot per change.

    A snapshot identical (in its slow-moving numbers) to the newest one already
    saved is skipped; a revised figure, a new fiscal year or a different
    company name becomes a new fact with its own, later `known_at`. Comparing
    with the NEWEST snapshot rather than keying on the numbers alone is
    deliberate: if the numbers go A, then B, then A again, the second A must
    be a new fact, otherwise it would dedupe into the first A (an older
    known_at) and a reader would wrongly still see B as the latest.

    known_at is the fetch time: no free source tells us when a figure was
    first public, so the fetch time is the earliest we can prove. No overview
    means nothing to save (a symbol without coverage, e.g. a crypto pair).
    `effective_at` stays empty: the source gives only a fiscal year number,
    not its period end, and a wrong date would be worse than none; the latest
    fiscal year is in the payload.
    """
    if overview is None:
        return ArchiveWriteResult()
    fetched = to_naive_utc(fetched_at) if fetched_at is not None else utcnow_naive()
    payload, fingerprint = _snapshot_payloads(overview, financial_years, source)

    latest = facts_known_as_of(session, FactKind.FUNDAMENTALS_SNAPSHOT, symbol=symbol, limit=1)
    if latest and latest[0].payload.get("fingerprint") == fingerprint:
        return ArchiveWriteResult(already_saved=1)

    result = record_fact(
        session,
        kind=FactKind.FUNDAMENTALS_SNAPSHOT,
        symbol=symbol,
        source=source,
        dedupe_key=make_dedupe_key(symbol.upper(), fingerprint, fetched.isoformat()),
        payload=payload,
        fetched_at=fetched,
    )
    return ArchiveWriteResult(new=int(result.created), already_saved=int(not result.created))


def archive_fetched_data(
    session: Session | None,
    symbol: str,
    *,
    overview: CompanyOverview | None,
    financial_years: Sequence[FinancialYear],
    news: Iterable[NewsItem],
    data_provider: object | None,
) -> ArchiveWriteResult:
    """Archive what a request just fetched. Safe to call from anywhere: it
    returns an empty result instead of raising, whatever goes wrong.

    Skipped (not an error) when there is no session, when a backtest is
    simulating a past moment, or when the session already holds uncommitted
    work (the archive commits, and must not commit or roll back somebody
    else's half-finished changes).
    """
    if session is None:
        return ArchiveWriteResult()
    if is_simulated():
        # A backtest replays the past; whatever it fetched must not be stamped
        # with today's fetch time and saved as if we had known it then.
        return ArchiveWriteResult()
    try:
        if session.new or session.dirty or session.deleted:
            logger.warning("archive skipped for %s: the session has uncommitted changes", symbol)
            return ArchiveWriteResult()
        source = _provider_label(data_provider)
        fetched = utcnow_naive()
        news_result = archive_news(session, symbol, news, source, fetched_at=fetched)
        fundamentals_result = archive_fundamentals(
            session, symbol, overview, financial_years, source, fetched_at=fetched
        )
        return ArchiveWriteResult(
            new=news_result.new + fundamentals_result.new,
            already_saved=news_result.already_saved + fundamentals_result.already_saved,
        )
    except Exception:  # noqa: BLE001 - the archive is best-effort by design
        logger.warning("archiving fetched data for %s failed; continuing without it", symbol, exc_info=True)
        try:
            # A failed flush leaves the session unusable until rolled back, and
            # the caller is about to use it for the real work.
            session.rollback()
        except Exception:  # noqa: BLE001
            logger.debug("rollback after a failed archive write also failed", exc_info=True)
        return ArchiveWriteResult()


# --- reading -----------------------------------------------------------------------------
# Thin wrappers over `facts_known_as_of`, so they inherit its look-ahead guard:
# nothing known after the cutoff is ever returned, and the cutoff defaults to
# the simulated moment inside `with as_of(...)`.


def archived_news(
    session: Session,
    symbol: str,
    as_of: datetime | None = None,
    since: datetime | None = None,
    limit: int | None = None,
) -> list[KnownFact]:
    """News saved for `symbol` that was public by `as_of` (default: now, or the
    simulated moment), newest first. `since` keeps only items known at or after
    it ("the last 7 days"). Note that archived news only exists from the day
    the archive began; an empty list for an older date means "not recorded",
    not "no news happened"."""
    return facts_known_as_of(session, FactKind.NEWS, as_of=as_of, symbol=symbol, since=since, limit=limit)


def latest_fundamentals_snapshot(
    session: Session, symbol: str, as_of: datetime | None = None
) -> KnownFact | None:
    """The newest fundamentals snapshot for `symbol` that we had saved by
    `as_of`, or None."""
    found = facts_known_as_of(session, FactKind.FUNDAMENTALS_SNAPSHOT, as_of=as_of, symbol=symbol, limit=1)
    return found[0] if found else None


def news_items_from_facts(facts: Iterable[KnownFact]) -> list[NewsItem]:
    """Archived news back in the shape the scorers take (`NewsItem`), order kept."""
    items: list[NewsItem] = []
    for fact in facts:
        p = fact.payload
        items.append(
            NewsItem(
                headline=str(p.get("headline", "")),
                source=str(p.get("publisher", "")),
                url=str(p.get("url", "")),
                published_at=str(p.get("published_at", "")),
            )
        )
    return items


def fundamentals_from_fact(fact: KnownFact) -> tuple[CompanyOverview, list[FinancialYear]]:
    """A saved snapshot back in the shape the scorers take. Missing numbers
    stay None; year rows with a missing revenue or income are dropped rather
    than turned into zeros."""
    p = fact.payload
    overview = CompanyOverview(
        symbol=fact.symbol or "",
        name=str(p.get("name", "")),
        market_cap=p.get("market_cap"),
        pe_ratio=p.get("pe_ratio"),
        revenue_ttm=p.get("revenue_ttm"),
        eps_ttm=p.get("eps_ttm"),
        week52_low=p.get("week52_low"),
        week52_high=p.get("week52_high"),
    )
    years = [
        FinancialYear(year=int(y["year"]), revenue=float(y["revenue"]), net_income=float(y["net_income"]))
        for y in p.get("financial_years", [])
        if y.get("revenue") is not None and y.get("net_income") is not None
    ]
    return overview, years


def _earliest(*values: datetime | None) -> datetime | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


def _latest(*values: datetime | None) -> datetime | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def get_archive_summary(session: Session, symbol: str, limit: int = 5) -> ArchiveResponse:
    """Read-only summary for the endpoint: counts, the most recent items and the
    archive's overall size. Dates are when we FIRST/LAST saved something
    (`fetched_at`), not the items' own publish times, because "since when has
    the archive been recording" is the question a backtest needs answered."""
    kinds = [FactKind.NEWS, FactKind.FUNDAMENTALS_SNAPSHOT]
    news_stats = fact_stats(session, FactKind.NEWS, symbol=symbol)
    fund_stats = fact_stats(session, FactKind.FUNDAMENTALS_SNAPSHOT, symbol=symbol)
    total_stats = fact_stats(session, kinds)
    news_total = fact_stats(session, FactKind.NEWS)
    fund_total = fact_stats(session, FactKind.FUNDAMENTALS_SNAPSHOT)

    recent_news = [
        ArchivedNewsSchema(
            headline=str(f.payload.get("headline", "")),
            publisher=str(f.payload.get("publisher", "")),
            url=str(f.payload.get("url", "")),
            published_at=str(f.payload.get("published_at", "")),
            known_at=f.known_at,
            known_at_basis=f.known_at_basis,
            fetched_at=f.fetched_at,
        )
        for f in archived_news(session, symbol, limit=limit)
    ]
    recent_fundamentals = []
    for f in facts_known_as_of(session, FactKind.FUNDAMENTALS_SNAPSHOT, symbol=symbol, limit=limit):
        p = f.payload
        recent_fundamentals.append(
            ArchivedFundamentalsSchema(
                known_at=f.known_at,
                fetched_at=f.fetched_at,
                revenue_ttm=p.get("revenue_ttm"),
                eps_ttm=p.get("eps_ttm"),
                market_cap=p.get("market_cap"),
                latest_fiscal_year=p.get("latest_fiscal_year"),
                financial_years=len(p.get("financial_years", [])),
            )
        )
    return ArchiveResponse(
        symbol=symbol,
        news_count=news_stats.count,
        fundamentals_count=fund_stats.count,
        first_archived_at=_earliest(news_stats.first_fetched_at, fund_stats.first_fetched_at),
        last_archived_at=_latest(news_stats.last_fetched_at, fund_stats.last_fetched_at),
        recent_news=recent_news,
        recent_fundamentals=recent_fundamentals,
        totals=ArchiveTotals(
            news_count=news_total.count,
            fundamentals_count=fund_total.count,
            symbols=total_stats.symbols,
            first_archived_at=total_stats.first_fetched_at,
            last_archived_at=total_stats.last_fetched_at,
        ),
    )
