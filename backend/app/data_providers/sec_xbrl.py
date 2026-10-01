"""Annual revenue from SEC's XBRL "company concept" API, dated by when it was FILED.

A company's yearly revenue is public the day its 10-K is filed, not on the last
day of its fiscal year, and a backtest that scores "revenue grew" has to see each
year's number only from that filing date on. This module downloads the annual
revenue values (with the filing date SEC records for each) and stores one dated
fact per value (`fundamentals_revenue`): `known_at` is the end of the filing day
in New York, `effective_at` is the period's last day.

    https://data.sec.gov/api/xbrl/companyconcept/CIK##########/us-gaap/<concept>.json

How the data looks, and what is done about it
---------------------------------------------
* Each 10-K reports the current year AND the two before it, so one fiscal year
  appears in up to three filings. Every appearance is stored as its own fact
  (key: company, concept, period end, accession). The reader
  (`backtest/fundamentals_history.py`) takes the latest filing known at the
  simulated moment, so a restated figure replaces the original only from the day
  the restating filing was made, and the original is what a trader saw before.
* The same file also holds quarterly figures (from 10-Qs and from the Q4 columns
  of a 10-K). Only values covering about a year (350-380 days) from a 10-K or a
  10-K/A are kept.
* Companies change which tag they report revenue under (the 2018 revenue
  standard moved many from SalesRevenueNet to
  RevenueFromContractWithCustomerExcludingAssessedTax). All of the tags in
  REVENUE_CONCEPTS are fetched and stored; the reader takes whichever filing is
  latest.
* The API gives no acceptance time, only the filing DATE, so a filing is treated
  as public from the very end of that day (New York time). A filing is usually
  accepted during the day, so this can only make the data appear later than it
  did, never earlier.

Requests go through the shared SEC client (identified User-Agent, at most 5 per
second, retries, a clean error when the source is down). A company with no
filings under a tag answers 404, which just means "none under that tag".
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlmodel import Session

from app.data_providers.base import DataProviderError
from app.data_providers.sec_client import SecClient, get_sec_client
from app.data_providers.sec_form4 import resolve_cik
from app.knowledge import FactKind, end_of_local_day_utc, make_dedupe_key, record_fact

logger = logging.getLogger(__name__)

SOURCE_NAME = "sec_edgar"
COMPANY_CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{concept}.json"
# Tried in this order; all that exist are stored. The order only breaks a tie when
# two tags were filed on the same day (see fundamentals_history).
REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)
ANNUAL_FORMS = frozenset({"10-K", "10-K/A"})
# A fiscal year is 52 or 53 weeks (364 or 371 days) or a calendar year (365 or
# 366). Anything outside this band is a quarter, a half or a transition period.
ANNUAL_MIN_DAYS = 350
ANNUAL_MAX_DAYS = 380
DEDUPE_PREFIX = "rev"


@dataclass(frozen=True)
class AnnualRevenue:
    """One annual revenue value as one filing reported it."""

    concept: str
    period_start: date
    period_end: date
    value: float
    filed: date
    accession: str
    form: str
    fiscal_year: int | None
    fiscal_period: str | None


def _day(raw) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def parse_annual_revenue(document: dict, concept: str) -> list[AnnualRevenue]:
    """The yearly values in one companyconcept document (USD only). Rows that are
    not whole-year 10-K figures, or that lack a date, are dropped."""
    rows = ((document.get("units") or {}).get("USD")) or []
    parsed: list[AnnualRevenue] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("form") not in ANNUAL_FORMS:
            continue
        start, end, filed = _day(row.get("start")), _day(row.get("end")), _day(row.get("filed"))
        value = row.get("val")
        if start is None or end is None or filed is None or not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if not ANNUAL_MIN_DAYS <= (end - start).days <= ANNUAL_MAX_DAYS:
            continue
        fiscal_year = row.get("fy")
        parsed.append(
            AnnualRevenue(
                concept=concept,
                period_start=start,
                period_end=end,
                value=float(value),
                filed=filed,
                accession=str(row.get("accn") or ""),
                form=str(row["form"]),
                fiscal_year=int(fiscal_year) if isinstance(fiscal_year, int) else None,
                fiscal_period=row.get("fp"),
            )
        )
    parsed.sort(key=lambda r: (r.period_end, r.filed, r.accession))
    return parsed


def fetch_annual_revenue(cik: int, client: SecClient | None = None) -> list[AnnualRevenue]:
    """Every annual revenue value SEC holds for `cik` under any of REVENUE_CONCEPTS.
    A tag the company never used (HTTP 404) is skipped; any other failure raises
    DataProviderError so the caller never mistakes a broken download for "no data"."""
    client = client or get_sec_client()
    rows: list[AnnualRevenue] = []
    for concept in REVENUE_CONCEPTS:
        try:
            document = client.get_json(COMPANY_CONCEPT_URL.format(cik=cik, concept=concept))
        except DataProviderError as exc:
            if "HTTP 404" in str(exc):
                continue
            raise
        if isinstance(document, dict):
            rows.extend(parse_annual_revenue(document, concept))
    return rows


def known_at_for(filed: date):
    """When a filing made on `filed` counts as public: the end of that day in New
    York (the API gives no acceptance time; see the module docstring)."""
    return end_of_local_day_utc(filed)


def ingest_annual_revenue(session: Session, symbol: str, cik: int, rows: list[AnnualRevenue]) -> tuple[int, int]:
    """Store `rows` as dated facts. Returns (created, already_stored); safe to repeat."""
    created = existing = 0
    for row in rows:
        result = record_fact(
            session,
            kind=FactKind.FUNDAMENTALS_REVENUE,
            symbol=symbol,
            source=SOURCE_NAME,
            source_ref=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}&type=10-K",
            dedupe_key=make_dedupe_key(DEDUPE_PREFIX, cik, row.concept, row.period_end.isoformat(), row.accession),
            known_at=known_at_for(row.filed),
            known_at_basis="derived",
            effective_at=_midnight(row.period_end),
            payload={
                "cik": cik,
                "concept": row.concept,
                "value": row.value,
                "period_start": row.period_start.isoformat(),
                "period_end": row.period_end.isoformat(),
                "filed": row.filed.isoformat(),
                "accession": row.accession,
                "form": row.form,
                "fiscal_year": row.fiscal_year,
                "fiscal_period": row.fiscal_period,
            },
        )
        if result.created:
            created += 1
        else:
            existing += 1
    return created, existing


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day)


@dataclass
class FundamentalsBackfillReport:
    symbols: int = 0
    facts_created: int = 0
    facts_already_stored: int = 0
    unknown_symbols: list[str] = field(default_factory=list)  # not an SEC registrant (no CIK)
    no_revenue_symbols: list[str] = field(default_factory=list)  # a registrant, but no annual revenue under any tag
    errors: list[str] = field(default_factory=list)


def backfill_fundamentals(
    session: Session,
    symbols: list[str],
    *,
    client: SecClient | None = None,
    cik_resolver: Callable[..., int | None] = resolve_cik,
    progress: Callable[[str, int, int], None] | None = None,
) -> FundamentalsBackfillReport:
    """Download and store the annual revenue history of each symbol. One symbol
    failing never stops the others; failures are listed in the report."""
    client = client or get_sec_client()
    report = FundamentalsBackfillReport()
    for index, raw in enumerate(symbols, start=1):
        symbol = raw.strip().upper()
        report.symbols += 1
        try:
            cik = cik_resolver(symbol, client=client)
            if cik is None:
                report.unknown_symbols.append(symbol)
                continue
            rows = fetch_annual_revenue(cik, client)
            if not rows:
                report.no_revenue_symbols.append(symbol)
                continue
            created, existing = ingest_annual_revenue(session, symbol, cik, rows)
            report.facts_created += created
            report.facts_already_stored += existing
        except DataProviderError as exc:
            logger.warning("revenue backfill failed for %s: %s", symbol, exc)
            report.errors.append(f"{symbol}: {exc}")
        finally:
            if progress is not None:
                progress(symbol, index, len(symbols))
    return report
