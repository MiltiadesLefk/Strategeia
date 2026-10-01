"""Revenue history as it stood at a past moment, for the backtester.

The facts come from `app.data_providers.sec_xbrl` (one `fundamentals_revenue` fact
per annual value per filing, `known_at` = the end of the filing day). This reader
rebuilds the list of fiscal years that live scoring reads (`FinancialYear`), using
only filings public at the cutoff:

* For each fiscal period (identified by its last day) the LATEST filing known at
  the cutoff wins. A company that restated a year in a later 10-K is therefore
  seen with the original number until that later filing is public, then with the
  restated one. Several filings on the same day: the tag listed first in
  REVENUE_CONCEPTS, then the accession number, breaks the tie so the answer never
  depends on row order.
* Periods are ordered by their end date, and only the newest unbroken run of
  years is kept: live scoring compares the last two entries as "year on year", so
  a gap (a year with no stored filing) must not turn into a two-year comparison
  labelled as one.
* If the newest known year ended more than STALE_AFTER_DAYS before the cutoff the
  history is treated as missing (a stored history that stopped long ago is a
  failed or partial download, and scoring from it would be scoring old news).
* Net income is not stored, so it is NaN (the scorer reads revenue only).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from sqlmodel import Session

from app.data_providers.base import FinancialYear
from app.data_providers.sec_xbrl import REVENUE_CONCEPTS
from app.knowledge import FactKind, KnownFact, current_as_of, facts_known_as_of, to_naive_utc

# The most years returned (live providers return about four).
MAX_YEARS = 4
# Consecutive fiscal years end 364-371 days apart; allow for a short or long year.
MAX_GAP_BETWEEN_YEARS_DAYS = 400
# Two years: a company's newest 10-K is at most a year and a quarter old, so more
# than this means the stored history has holes at its recent end.
STALE_AFTER_DAYS = 730


@dataclass(frozen=True)
class KnownAnnualRevenue:
    period_end: date
    value: float
    filed: date
    concept: str
    accession: str
    known_at: datetime


def _concept_rank(concept: str) -> int:
    try:
        return REVENUE_CONCEPTS.index(concept)
    except ValueError:
        return len(REVENUE_CONCEPTS)


def _to_known(fact: KnownFact) -> KnownAnnualRevenue | None:
    p = fact.payload or {}
    try:
        return KnownAnnualRevenue(
            period_end=date.fromisoformat(p["period_end"]),
            value=float(p["value"]),
            filed=date.fromisoformat(p["filed"]),
            concept=str(p.get("concept") or ""),
            accession=str(p.get("accession") or ""),
            known_at=fact.known_at,
        )
    except (KeyError, TypeError, ValueError):
        return None


def known_annual_revenue(session: Session, symbol: str, as_of: datetime | None = None) -> list[KnownAnnualRevenue]:
    """One value per fiscal period: the latest filing public at the cutoff, oldest period first."""
    best: dict[date, KnownAnnualRevenue] = {}
    for fact in facts_known_as_of(session, FactKind.FUNDAMENTALS_REVENUE, as_of=as_of, symbol=symbol):
        row = _to_known(fact)
        if row is None:
            continue
        current = best.get(row.period_end)
        if current is None or _preference(row) > _preference(current):
            best[row.period_end] = row
    return [best[end] for end in sorted(best)]


def _preference(row: KnownAnnualRevenue) -> tuple:
    # Later filing wins; on a tie the earlier-listed tag, then the larger accession.
    return (row.filed, -_concept_rank(row.concept), row.accession)


def revenue_history_as_of(session: Session, symbol: str, as_of: datetime | None = None) -> list[FinancialYear]:
    """Fiscal years known at the cutoff, oldest first, in the shape live scoring reads.
    Empty when nothing usable was public."""
    cutoff = to_naive_utc(as_of) if as_of is not None else current_as_of()
    rows = known_annual_revenue(session, symbol, as_of)
    if not rows:
        return []
    if (cutoff.date() - rows[-1].period_end).days > STALE_AFTER_DAYS:
        return []
    chain = [rows[-1]]
    for row in reversed(rows[:-1]):
        if (chain[0].period_end - row.period_end).days > MAX_GAP_BETWEEN_YEARS_DAYS:
            break
        chain.insert(0, row)
    return [
        FinancialYear(year=row.period_end.year, revenue=row.value, net_income=math.nan) for row in chain[-MAX_YEARS:]
    ]

