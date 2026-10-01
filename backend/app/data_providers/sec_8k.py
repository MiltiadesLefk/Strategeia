"""SEC 8-K filings (company announcements) as dated facts.

An 8-K is the form a US-listed company files within four business days of a
material event: results, a new or ended major contract, an officer leaving, a
delisting notice, a bankruptcy, a restatement. Each filing lists numbered ITEM
CODES ("2.02" is results, "5.02" is a director or officer change), and the
company's submissions index already carries them in an `items` string such as
"2.02,9.01". That is enough to classify a filing without reading its text, so
rules (and a later backtest) can use announcements without any AI.

What is stored: one `sec_filing_8k` fact per filing, with

  known_at     = the SEC acceptance time (when it became public; basis "source")
  effective_at = the report date (the event the filing is about; often earlier
                 than the filing, never a visibility cutoff)
  payload      = accession, form, item codes, item titles, coarse categories,
                 primary document URL, filing index URL

This module has the same four parts as the Form 4 module next to it: list the
filings for one company (`list_8k_filings`), ingest one (`ingest_8k_filing`),
bulk-load a history (`backfill_8k`), and read them back as of any moment
(`filings_8k_as_of`, which goes through `facts_known_as_of`, so a filing accepted
after the cutoff is invisible).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from app.data_providers.base import DataProviderError
from app.data_providers.sec_client import SecClient, get_sec_client
from app.data_providers.sec_form4 import (
    ARCHIVE_URL,
    SOURCE_NAME,
    SUBMISSIONS_PAGE_URL,
    SUBMISSIONS_URL,
    _date,
    parse_acceptance_time,
    resolve_cik,
)
from app.knowledge import FactKind, KnownFact, current_as_of, facts_known_as_of, make_dedupe_key, record_fact, to_naive_utc

logger = logging.getLogger(__name__)

# The original filing and its amendment. An amendment is its own filing with its
# own accession number and acceptance time, so it is stored as its own fact.
FORM_8K = "8-K"
FORM_8KA = "8-K/A"
FORM_8K_FORMS = frozenset({FORM_8K, FORM_8KA})

# Dedupe key is "8k|<accession>": one fact per filing.
DEDUPE_PREFIX = "8k"
DEFAULT_WINDOW_DAYS = 90

# Where a filing's human-readable page lives (shows every document in it).
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{accession}-index.htm"

# Coarse categories, so a rule can ask "any leadership news?" without knowing
# the item numbers.
CATEGORY_RESULTS = "results"
CATEGORY_LEADERSHIP = "leadership"
CATEGORY_AGREEMENT = "agreement"
CATEGORY_RESTRUCTURING = "restructuring"
CATEGORY_REGULATORY = "regulatory"
CATEGORY_OTHER = "other"
CATEGORIES = (
    CATEGORY_RESULTS,
    CATEGORY_LEADERSHIP,
    CATEGORY_AGREEMENT,
    CATEGORY_RESTRUCTURING,
    CATEGORY_REGULATORY,
    CATEGORY_OTHER,
)

# item code -> (title as the SEC's form lists it, shortened; category). The full
# list of 8-K items is fixed by the form's instructions; an unknown code (a new
# item the SEC adds) is kept and labelled as unlisted rather than dropped.
ITEM_DICTIONARY: dict[str, tuple[str, str]] = {
    "1.01": ("Entry into a material definitive agreement", CATEGORY_AGREEMENT),
    "1.02": ("Termination of a material definitive agreement", CATEGORY_AGREEMENT),
    "1.03": ("Bankruptcy or receivership", CATEGORY_RESTRUCTURING),
    "1.04": ("Mine safety: shutdowns and patterns of violations", CATEGORY_REGULATORY),
    "1.05": ("Material cybersecurity incident", CATEGORY_OTHER),
    "2.01": ("Completion of an acquisition or disposition of assets", CATEGORY_RESTRUCTURING),
    "2.02": ("Results of operations and financial condition", CATEGORY_RESULTS),
    "2.03": ("Creation of a direct financial obligation", CATEGORY_OTHER),
    "2.04": ("Triggering events that accelerate a financial obligation", CATEGORY_OTHER),
    "2.05": ("Costs of exit or disposal activities", CATEGORY_RESTRUCTURING),
    "2.06": ("Material impairments", CATEGORY_RESULTS),
    "3.01": ("Notice of delisting or failure to meet a listing rule", CATEGORY_REGULATORY),
    "3.02": ("Unregistered sales of equity securities", CATEGORY_OTHER),
    "3.03": ("Material modification to rights of security holders", CATEGORY_OTHER),
    "4.01": ("Change in the company's auditor", CATEGORY_REGULATORY),
    "4.02": ("Non-reliance on previously issued financial statements", CATEGORY_REGULATORY),
    "5.01": ("Change in control of the company", CATEGORY_RESTRUCTURING),
    "5.02": ("Director or officer departure, election or compensation change", CATEGORY_LEADERSHIP),
    "5.03": ("Amendment to articles or bylaws; change of fiscal year", CATEGORY_OTHER),
    "5.04": ("Temporary suspension of trading under an employee plan", CATEGORY_OTHER),
    "5.05": ("Amendment to the code of ethics", CATEGORY_OTHER),
    "5.06": ("Change in shell company status", CATEGORY_OTHER),
    "5.07": ("Shareholder vote results", CATEGORY_OTHER),
    "5.08": ("Shareholder director nominations", CATEGORY_OTHER),
    "6.01": ("Asset-backed securities: informational material", CATEGORY_OTHER),
    "6.02": ("Asset-backed securities: change of servicer or trustee", CATEGORY_OTHER),
    "6.03": ("Asset-backed securities: change in credit enhancement", CATEGORY_OTHER),
    "6.04": ("Asset-backed securities: failure to make a distribution", CATEGORY_OTHER),
    "6.05": ("Asset-backed securities: securities act updating disclosure", CATEGORY_OTHER),
    "7.01": ("Regulation FD disclosure", CATEGORY_OTHER),
    "8.01": ("Other events", CATEGORY_OTHER),
    "9.01": ("Financial statements and exhibits", CATEGORY_OTHER),
}
UNLISTED_ITEM_TITLE = "Unlisted item"


def item_title(code: str) -> str:
    return ITEM_DICTIONARY.get(code, (UNLISTED_ITEM_TITLE, CATEGORY_OTHER))[0]


def item_category(code: str) -> str:
    return ITEM_DICTIONARY.get(code, (UNLISTED_ITEM_TITLE, CATEGORY_OTHER))[1]


def parse_items(raw: str | None) -> list[str]:
    """The submissions index's `items` string ("2.02,9.01") -> ["2.02", "9.01"].
    Order kept, duplicates and blanks dropped; an empty or missing value (older
    filings carry none) is an empty list."""
    if not raw:
        return []
    out: list[str] = []
    for part in str(raw).split(","):
        code = part.strip()
        if code and code not in out:
            out.append(code)
    return out


@dataclass(frozen=True)
class Filing8K:
    cik: int
    accession: str  # with dashes: 0000320193-26-000018
    form: str  # "8-K" | "8-K/A"
    filing_date: date
    accepted_at: datetime  # naive UTC: when SEC accepted it, i.e. when it became public
    primary_document: str
    items: tuple[str, ...]
    report_date: date | None = None

    @property
    def url(self) -> str:
        return ARCHIVE_URL.format(
            cik=self.cik, accession=self.accession.replace("-", ""), document=self.primary_document
        )

    @property
    def index_url(self) -> str:
        return index_url(self.cik, self.accession)


def index_url(cik: int, accession: str) -> str:
    return INDEX_URL.format(cik=cik, accession_nodash=accession.replace("-", ""), accession=accession)


def filings_from_columns(cik: int, columns: dict) -> list[Filing8K]:
    """8-K rows of one submissions block (`filings.recent` or an older page)."""
    forms = columns.get("form") or []
    accessions = columns.get("accessionNumber") or []
    filed = columns.get("filingDate") or []
    accepted = columns.get("acceptanceDateTime") or []
    documents = columns.get("primaryDocument") or []
    reports = columns.get("reportDate") or []
    items = columns.get("items") or []
    out: list[Filing8K] = []
    for i, form in enumerate(forms):
        if form not in FORM_8K_FORMS:
            continue
        try:
            filing_date = _date(filed[i])
            accepted_at = parse_acceptance_time(accepted[i])
            accession = accessions[i]
            document = documents[i]
        except (IndexError, ValueError, TypeError):
            logger.warning("skipping a malformed 8-K row (index %d)", i)
            continue
        if filing_date is None or not accession or not document:
            continue
        out.append(
            Filing8K(
                cik=cik,
                accession=accession,
                form=form,
                filing_date=filing_date,
                accepted_at=accepted_at,
                primary_document=document,
                items=tuple(parse_items(items[i] if i < len(items) else None)),
                report_date=_date(reports[i]) if i < len(reports) else None,
            )
        )
    return out


def list_8k_filings(
    cik: int,
    since: date | None = None,
    until: date | None = None,
    *,
    client: SecClient | None = None,
    include_older_pages: bool = True,
) -> list[Filing8K]:
    """8-K and 8-K/A filings for a company filed within [since, until] (by filing
    date, inclusive), newest first. The submissions index keeps about the latest
    thousand filings in `recent`; older ones sit in extra pages, fetched only
    when their date range overlaps the request. `include_older_pages=False` is
    for a poller that only wants the last few days: always exactly one request."""
    client = client or get_sec_client()
    index = client.get_json(SUBMISSIONS_URL.format(cik=cik))
    if not isinstance(index, dict):
        raise DataProviderError(f"unexpected submissions payload for CIK {cik}")
    return filings_from_index(cik, index, since, until, client=client if include_older_pages else None)


def filings_from_index(
    cik: int, index: dict, since: date | None = None, until: date | None = None, *, client: SecClient | None = None
) -> list[Filing8K]:
    """The 8-Ks in an already-fetched submissions payload (so a caller that needs
    both Form 4s and 8-Ks pays for one request). With a `client`, older pages that
    overlap the range are fetched too."""
    block = index.get("filings") or {}
    found = filings_from_columns(cik, block.get("recent") or {})
    if client is not None:
        for page in block.get("files") or []:
            page_from, page_to = _date(page.get("filingFrom")), _date(page.get("filingTo"))
            if since and page_to and page_to < since:
                continue
            if until and page_from and page_from > until:
                continue
            name = page.get("name")
            if not name:
                continue
            columns = client.get_json(SUBMISSIONS_PAGE_URL.format(name=name))
            if isinstance(columns, dict):
                found.extend(filings_from_columns(cik, columns))
    selected = {
        f.accession: f
        for f in found
        if (since is None or f.filing_date >= since) and (until is None or f.filing_date <= until)
    }
    return sorted(selected.values(), key=lambda f: (f.accepted_at, f.accession), reverse=True)


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Ingest8KResult:
    accession: str
    created: bool
    fact: KnownFact


def ingest_8k_filing(session: Session, symbol: str, filing: Filing8K) -> Ingest8KResult:
    """Store one 8-K as a `sec_filing_8k` fact, idempotently (a re-run adds
    nothing). No download is needed: the item codes come from the submissions
    index, so a backfill of a company's whole history is a handful of requests."""
    codes = list(filing.items)
    recorded = record_fact(
        session,
        kind=FactKind.SEC_FILING_8K,
        symbol=symbol,
        source=SOURCE_NAME,
        source_ref=filing.index_url,
        dedupe_key=make_dedupe_key(DEDUPE_PREFIX, filing.accession),
        known_at=filing.accepted_at,
        known_at_basis="source",
        effective_at=datetime.combine(filing.report_date, datetime.min.time()) if filing.report_date else None,
        payload={
            "accession": filing.accession,
            "form": filing.form,
            "cik": filing.cik,
            "symbol": symbol.strip().upper(),
            "filing_date": filing.filing_date.isoformat(),
            "report_date": filing.report_date.isoformat() if filing.report_date else None,
            "items": codes,
            "item_titles": [item_title(c) for c in codes],
            "categories": sorted({item_category(c) for c in codes}),
            "primary_document_url": filing.url,
            "index_url": filing.index_url,
        },
    )
    return Ingest8KResult(accession=filing.accession, created=recorded.created, fact=recorded.fact)


def ingested_8k_accessions(session: Session, symbol: str) -> set[str]:
    """Accession numbers already stored for `symbol` (writer-side bookkeeping read
    from the dedupe keys only, so it cannot leak the content of a fact into a
    decision)."""
    keys = session.exec(
        select(KnownFact.dedupe_key).where(
            KnownFact.kind == FactKind.SEC_FILING_8K, KnownFact.symbol == symbol.strip().upper()
        )
    ).all()
    prefix = DEDUPE_PREFIX + "|"
    return {k[len(prefix) :] for k in keys if k.startswith(prefix)}


# --------------------------------------------------------------------------
# Backfill
# --------------------------------------------------------------------------


@dataclass
class Backfill8KProgress:
    symbol: str
    filings_total: int
    filings_done: int
    facts_created: int
    skipped_existing: int


@dataclass
class Backfill8KReport:
    symbols: int = 0
    filings_seen: int = 0
    filings_ingested: int = 0
    filings_skipped_existing: int = 0
    errors: list[str] = field(default_factory=list)
    unknown_symbols: list[str] = field(default_factory=list)


def backfill_8k(
    session: Session,
    symbols: Iterable[str],
    since: date,
    until: date | None = None,
    *,
    client: SecClient | None = None,
    progress: Callable[[Backfill8KProgress], None] | None = None,
    ciks: dict[str, int] | None = None,
) -> Backfill8KReport:
    """Load every 8-K filed in [since, until] for each symbol.

    Resumable and safe to re-run: accessions already stored are skipped, and the
    insert is idempotent anyway. A symbol that fails is reported and skipped
    rather than aborting the run. Politeness is the shared client's job (a
    process-wide limit of 5 requests per second, retries with backoff); one
    company costs one request plus one per older page that overlaps the range."""
    client = client or get_sec_client()
    report = Backfill8KReport()
    for raw_symbol in symbols:
        symbol = raw_symbol.strip().upper()
        if not symbol:
            continue
        report.symbols += 1
        try:
            cik = (ciks or {}).get(symbol) or resolve_cik(symbol, client=client)
            if cik is None:
                report.unknown_symbols.append(symbol)
                continue
            filings = list_8k_filings(cik, since, until, client=client)
        except DataProviderError as exc:
            report.errors.append(f"{symbol}: {exc}")
            continue
        have = ingested_8k_accessions(session, symbol)
        report.filings_seen += len(filings)
        done = created = already = 0
        # Oldest first: an interrupted run leaves a contiguous history.
        for filing in sorted(filings, key=lambda f: (f.accepted_at, f.accession)):
            if filing.accession in have:
                already += 1
                report.filings_skipped_existing += 1
            else:
                result = ingest_8k_filing(session, symbol, filing)
                if result.created:
                    created += 1
                report.filings_ingested += 1
            done += 1
            if progress is not None:
                progress(
                    Backfill8KProgress(
                        symbol=symbol,
                        filings_total=len(filings),
                        filings_done=done,
                        facts_created=created,
                        skipped_existing=already,
                    )
                )
    return report


# --------------------------------------------------------------------------
# Reading as of a moment
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredFiling8K:
    """One 8-K as known at the cutoff the caller asked about."""

    symbol: str
    accession: str
    form: str
    known_at: datetime  # SEC acceptance time (naive UTC)
    report_date: date | None
    items: tuple[str, ...]
    item_titles: tuple[str, ...]
    categories: tuple[str, ...]
    url: str | None  # the filing's index page


def _to_stored(fact: KnownFact) -> StoredFiling8K:
    p = fact.payload or {}
    report = None
    if p.get("report_date"):
        try:
            report = date.fromisoformat(p["report_date"])
        except ValueError:
            report = None
    return StoredFiling8K(
        symbol=fact.symbol or p.get("symbol") or "",
        accession=p.get("accession") or "",
        form=p.get("form") or FORM_8K,
        known_at=fact.known_at,
        report_date=report,
        items=tuple(p.get("items") or ()),
        item_titles=tuple(p.get("item_titles") or ()),
        categories=tuple(p.get("categories") or ()),
        url=fact.source_ref or p.get("index_url"),
    )


def has_8k_data(session: Session, symbol: str, as_of: datetime | None = None) -> bool:
    """Whether any 8-K for `symbol` was known at the cutoff at all: the data was
    loaded, as opposed to "the company filed nothing lately"."""
    return bool(facts_known_as_of(session, FactKind.SEC_FILING_8K, as_of=as_of, symbol=symbol, limit=1))


def filings_8k_as_of(
    session: Session,
    symbol: str,
    as_of: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    items: Iterable[str] | None = None,
) -> list[StoredFiling8K]:
    """8-Ks accepted in the `window_days` before the cutoff, newest first. With
    `items`, only filings listing at least one of those item codes. Inside
    `with as_of(t):` the `as_of` argument can be left out."""
    cutoff = to_naive_utc(as_of) if as_of is not None else current_as_of()
    facts = facts_known_as_of(
        session,
        FactKind.SEC_FILING_8K,
        as_of=as_of,
        symbol=symbol,
        since=cutoff - timedelta(days=window_days),
    )
    wanted = set(items) if items is not None else None
    stored = [_to_stored(f) for f in facts]
    if wanted is not None:
        stored = [s for s in stored if wanted.intersection(s.items)]
    return stored
