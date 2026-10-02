"""SEC Schedule 13D and 13G: who crossed 5% of a company, stored with the time the
filing was published.

Anyone who comes to own more than 5% of a company's voting shares has to say so.
Two forms exist and they mean different things:

  * Schedule 13D: the holder may want to influence the company (an activist, an
    acquirer). Since 2024 it is due within 5 business days of crossing 5%, and
    it carries a statement of purpose (Item 4), which is the interesting part.
  * Schedule 13G: the short form for holders that say they are passive (index
    funds, asset managers). Vanguard and BlackRock file these for almost every
    large company, and they are routine, not news.

Since December 2024 both forms are filed as structured XML, which is what this
module reads. Older filings (listed as "SC 13D" / "SC 13G") are free-text
documents; they are counted in the listing and left unread, because guessing a
percentage out of prose is how wrong numbers get stored.

The stake is usually small news for a mega-cap: no individual owns 5% of Apple,
and the filers that do are index managers. The signal is richest for smaller
companies, which is why the page says so plainly.

Facts: one `ownership_filing` fact per filing, `known_at` = SEC acceptance time,
`effective_at` = the event date (the day the holder crossed or changed). The
headline percentage and share count are those of the filing's largest reporting
person; every reporting person is kept in the payload (a 13D is often filed
jointly by a fund, its manager and the manager's owner, who all report the same
shares).
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from app.data_providers.base import DataProviderError
from app.data_providers.sec_client import SecClient, get_sec_client
from app.data_providers.sec_form4 import (
    SUBMISSIONS_PAGE_URL,
    SUBMISSIONS_URL,
    TICKER_MAP_URL,
    parse_acceptance_time,
)
from app.knowledge import FactKind, KnownFact, make_dedupe_key, record_fact

logger = logging.getLogger(__name__)

SOURCE_NAME = "sec_edgar"
ARCHIVE_DIR_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}"

FORM_13D = "SCHEDULE 13D"
FORM_13D_A = "SCHEDULE 13D/A"
FORM_13G = "SCHEDULE 13G"
FORM_13G_A = "SCHEDULE 13G/A"
OWNERSHIP_FORMS = frozenset({FORM_13D, FORM_13D_A, FORM_13G, FORM_13G_A})
# The free-text forms from before the structured ones (not parsed, only counted).
LEGACY_FORMS = frozenset({"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"})

DEDUPE_PREFIX = "13dg"
# Item 4 of a 13D can run to many pages; the page needs the opening, and the
# filing link has the rest.
MAX_PURPOSE_CHARS = 1500
MAX_COMMENT_CHARS = 400

# Without a filing to resume from, how far back a first look goes.
DEFAULT_LOOKBACK_DAYS = 90
MAX_FILINGS_PER_COMPANY = 25

# The ticker file changes rarely: hold the reverse map in memory for a day.
TICKER_MAP_TTL_SECONDS = 24 * 60 * 60

_XSL_PREFIX_RE = re.compile(r"^xsl[^/]*/")
_FORBIDDEN_XML_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


# --------------------------------------------------------------------------
# 1. Parsing
# --------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_child(node: ET.Element | None, *names: str) -> ET.Element | None:
    """First direct child whose local name equals one of `names`, ignoring case
    (the two schemas disagree: `issuerCik` in one, `issuerCIK` in the other)."""
    if node is None:
        return None
    wanted = {n.lower() for n in names}
    for child in node:
        if _local(child.tag).lower() in wanted:
            return child
    return None


def _children(node: ET.Element | None, *names: str) -> list[ET.Element]:
    if node is None:
        return []
    wanted = {n.lower() for n in names}
    return [c for c in node if _local(c.tag).lower() in wanted]


def _text(node: ET.Element | None) -> str | None:
    if node is None:
        return None
    cleaned = " ".join("".join(node.itertext()).split())
    return cleaned or None


def _child_text(node: ET.Element | None, *names: str) -> str | None:
    return _text(_find_child(node, *names))


def _number(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _us_date(raw: str | None) -> date | None:
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def _cik(raw: str | None) -> str | None:
    cleaned = (raw or "").strip().lstrip("0")
    return cleaned if cleaned.isdigit() else None


@dataclass
class ReportingPerson:
    name: str
    cik: str | None
    shares: float | None  # aggregate amount beneficially owned
    percent: float | None  # of the class
    sole_voting: float | None = None
    shared_voting: float | None = None
    sole_dispositive: float | None = None
    shared_dispositive: float | None = None
    person_type: str | None = None  # IA, IN, CO, OO ...


@dataclass
class OwnershipDocument:
    schedule: str  # "13D" | "13G"
    submission_type: str  # "SCHEDULE 13D/A" ...
    is_amendment: bool
    amendment_no: int | None
    filer_cik: str | None
    issuer_cik: str | None
    issuer_name: str | None
    issuer_cusip: str | None
    class_title: str | None
    event_date: date | None
    rule: str | None  # 13G: the rule it is filed under (13d-1(b) institution, (c) passive, (d) exempt)
    persons: list[ReportingPerson]
    purpose: str | None  # 13D Item 4
    previous_accession: str | None

    @property
    def headline(self) -> ReportingPerson | None:
        """The reporting person with the largest holding: the filing's headline stake."""
        ranked = [p for p in self.persons if p.shares is not None or p.percent is not None]
        if not ranked:
            return self.persons[0] if self.persons else None
        return max(ranked, key=lambda p: (p.shares or 0.0, p.percent or 0.0))


def _person_13g(node: ET.Element) -> ReportingPerson:
    powers = _find_child(node, "reportingPersonBeneficiallyOwnedNumberOfShares")
    return ReportingPerson(
        name=_child_text(node, "reportingPersonName") or "",
        cik=None,
        shares=_number(_child_text(node, "reportingPersonBeneficiallyOwnedAggregateNumberOfShares")),
        percent=_number(_child_text(node, "classPercent")),
        sole_voting=_number(_child_text(powers, "soleVotingPower")),
        shared_voting=_number(_child_text(powers, "sharedVotingPower")),
        sole_dispositive=_number(_child_text(powers, "soleDispositivePower")),
        shared_dispositive=_number(_child_text(powers, "sharedDispositivePower")),
        person_type=_child_text(node, "typeOfReportingPerson"),
    )


def _person_13d(node: ET.Element) -> ReportingPerson:
    return ReportingPerson(
        name=_child_text(node, "reportingPersonName") or "",
        cik=_cik(_child_text(node, "reportingPersonCIK")),
        shares=_number(_child_text(node, "aggregateAmountOwned")),
        percent=_number(_child_text(node, "percentOfClass")),
        sole_voting=_number(_child_text(node, "soleVotingPower")),
        shared_voting=_number(_child_text(node, "sharedVotingPower")),
        sole_dispositive=_number(_child_text(node, "soleDispositivePower")),
        shared_dispositive=_number(_child_text(node, "sharedDispositivePower")),
        person_type=_child_text(node, "typeOfReportingPerson"),
    )


def parse_ownership_xml(xml: bytes | str) -> OwnershipDocument:
    """Parse one structured Schedule 13D or 13G (or an amendment). Raises
    DataProviderError on anything else (a free-text filing, a different form), so
    a caller can skip it and carry on."""
    raw = xml.encode("utf-8") if isinstance(xml, str) else xml
    if _FORBIDDEN_XML_RE.search(raw):
        raise DataProviderError("ownership document declares a DOCTYPE or entity; refusing to parse it")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise DataProviderError(f"ownership document is not valid XML: {exc}") from exc
    if _local(root.tag) != "edgarSubmission":
        raise DataProviderError(f"not an EDGAR submission (root element {_local(root.tag)!r})")

    header = _find_child(root, "headerData")
    submission_type = (_child_text(header, "submissionType") or "").upper()
    if "13D" in submission_type:
        schedule = "13D"
    elif "13G" in submission_type:
        schedule = "13G"
    else:
        raise DataProviderError(f"not a Schedule 13D/13G (submission type {submission_type!r})")

    filer_info = _find_child(header, "filerInfo")
    filer = _find_child(filer_info, "filer")
    credentials = _find_child(filer, "filerCredentials")
    form_data = _find_child(root, "formData")
    cover = _find_child(form_data, "coverPageHeader")
    issuer = _find_child(cover, "issuerInfo")
    cusips = _find_child(issuer, "issuerCusips")
    rules = _find_child(cover, "designateRulesPursuantThisScheduleFiled")

    if schedule == "13G":
        persons = [_person_13g(n) for n in _children(form_data, "coverPageHeaderReportingPersonDetails")]
        event = _us_date(_child_text(cover, "eventDateRequiresFilingThisStatement"))
        purpose = None
        amendment_no = None
    else:
        persons = [_person_13d(n) for n in _children(_find_child(form_data, "reportingPersons"), "reportingPersonInfo")]
        event = _us_date(_child_text(cover, "dateOfEvent"))
        item4 = _find_child(_find_child(form_data, "items1To7"), "item4")
        purpose = _child_text(item4, "transactionPurpose")
        if purpose and len(purpose) > MAX_PURPOSE_CHARS:
            purpose = purpose[: MAX_PURPOSE_CHARS - 1].rstrip() + "…"
        amendment_no_raw = _number(_child_text(cover, "amendmentNo"))
        amendment_no = int(amendment_no_raw) if amendment_no_raw is not None else None

    rule_nodes = _children(rules, "designateRulePursuantThisScheduleFiled")
    return OwnershipDocument(
        schedule=schedule,
        submission_type=submission_type,
        is_amendment=submission_type.endswith("/A"),
        amendment_no=amendment_no,
        filer_cik=_cik(_child_text(credentials, "cik")),
        issuer_cik=_cik(_child_text(issuer, "issuerCik")),
        issuer_name=_child_text(issuer, "issuerName"),
        issuer_cusip=_child_text(cusips, "issuerCusipNumber"),
        class_title=_child_text(cover, "securitiesClassTitle"),
        event_date=event,
        rule=_text(rule_nodes[0]) if rule_nodes else None,
        persons=persons,
        purpose=purpose,
        previous_accession=_child_text(header, "previousAccessionNumber"),
    )


# --------------------------------------------------------------------------
# 2. The filing list
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OwnershipFiling:
    cik: int  # the CIK whose archive folder holds the document (company or filer)
    accession: str
    form: str
    filing_date: date
    accepted_at: datetime  # naive UTC
    primary_document: str

    @property
    def directory_url(self) -> str:
        return ARCHIVE_DIR_URL.format(cik=self.cik, accession_nodash=self.accession.replace("-", ""))

    @property
    def url(self) -> str:
        return f"{self.directory_url}/{self.primary_document}"

    @property
    def index_url(self) -> str:
        return f"{self.directory_url}/{self.accession}-index.htm"


@dataclass
class OwnershipListing:
    filings: list[OwnershipFiling]  # structured forms, newest first
    legacy_count: int  # free-text forms in the same window, not read
    name: str | None = None


def _iso(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _filings_from_columns(cik: int, columns: dict) -> tuple[list[OwnershipFiling], list[date]]:
    forms = columns.get("form") or []
    accessions = columns.get("accessionNumber") or []
    filed = columns.get("filingDate") or []
    accepted = columns.get("acceptanceDateTime") or []
    documents = columns.get("primaryDocument") or []
    out: list[OwnershipFiling] = []
    legacy: list[date] = []
    for i, form in enumerate(forms):
        if form in LEGACY_FORMS:
            filing_date = _iso(filed[i]) if i < len(filed) else None
            if filing_date:
                legacy.append(filing_date)
            continue
        if form not in OWNERSHIP_FORMS:
            continue
        try:
            filing_date = _iso(filed[i])
            accepted_at = parse_acceptance_time(accepted[i])
            accession = accessions[i]
            document = _XSL_PREFIX_RE.sub("", documents[i]) or "primary_doc.xml"
        except (IndexError, ValueError, TypeError):
            logger.warning("skipping a malformed submissions row (form %s, index %d)", form, i)
            continue
        if filing_date is None or not accession:
            continue
        out.append(OwnershipFiling(cik, accession, form, filing_date, accepted_at, document))
    return out, legacy


def list_ownership_filings(
    cik: int,
    since: date | None = None,
    until: date | None = None,
    *,
    client: SecClient | None = None,
) -> OwnershipListing:
    """13D/13G filings listed under `cik` (a company: filings ABOUT it; a filer:
    filings BY it) within [since, until] by filing date, newest first."""
    client = client or get_sec_client()
    index = client.get_json(SUBMISSIONS_URL.format(cik=cik))
    if not isinstance(index, dict):
        raise DataProviderError(f"unexpected submissions payload for CIK {cik}")
    block = index.get("filings") or {}
    filings, legacy = _filings_from_columns(cik, block.get("recent") or {})
    for page in block.get("files") or []:
        page_from, page_to = _iso(page.get("filingFrom")), _iso(page.get("filingTo"))
        if since and page_to and page_to < since:
            continue
        if until and page_from and page_from > until:
            continue
        name = page.get("name")
        if not name:
            continue
        columns = client.get_json(SUBMISSIONS_PAGE_URL.format(name=name))
        if isinstance(columns, dict):
            more, more_legacy = _filings_from_columns(cik, columns)
            filings.extend(more)
            legacy.extend(more_legacy)
    keep = {
        f.accession: f
        for f in filings
        if (since is None or f.filing_date >= since) and (until is None or f.filing_date <= until)
    }
    legacy_in_window = [d for d in legacy if (since is None or d >= since) and (until is None or d <= until)]
    ordered = sorted(keep.values(), key=lambda f: (f.accepted_at, f.accession), reverse=True)
    name = index.get("name")
    return OwnershipListing(ordered, len(legacy_in_window), str(name) if name else None)


# --------------------------------------------------------------------------
# CIK -> ticker (the reverse of the ticker file)
# --------------------------------------------------------------------------

_cik_ticker_map: tuple[float, dict[int, str]] | None = None


def reset_cik_ticker_cache() -> None:
    global _cik_ticker_map
    _cik_ticker_map = None


def ticker_for_cik(cik: int | str | None, *, client: SecClient | None = None, now: Callable[[], float] | None = None) -> str | None:
    """The ticker SEC lists for a company CIK, or None (a company with no listed
    stock, or one that has left the file). When a company has several tickers
    the first listed is used."""
    global _cik_ticker_map
    if cik is None:
        return None
    try:
        key = int(str(cik).strip().lstrip("0") or 0)
    except ValueError:
        return None
    clock = now or time.monotonic
    current = clock()
    if _cik_ticker_map is None or current - _cik_ticker_map[0] > TICKER_MAP_TTL_SECONDS:
        payload = (client or get_sec_client()).get_json(TICKER_MAP_URL)
        if not isinstance(payload, dict):
            raise DataProviderError("sec ticker map had an unexpected shape")
        mapping: dict[int, str] = {}
        for entry in payload.values():
            if isinstance(entry, dict) and entry.get("ticker") and entry.get("cik_str") is not None:
                mapping.setdefault(int(entry["cik_str"]), str(entry["ticker"]).upper())
        _cik_ticker_map = (current, mapping)
    return _cik_ticker_map[1].get(key)


# --------------------------------------------------------------------------
# 3. Ingest
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OwnershipIngestResult:
    accession: str
    created: bool
    fact: KnownFact | None = None
    document: OwnershipDocument | None = None


def _person_payload(p: ReportingPerson) -> dict:
    return {
        "name": p.name,
        "cik": p.cik,
        "shares": p.shares,
        "percent": p.percent,
        "sole_voting": p.sole_voting,
        "shared_voting": p.shared_voting,
        "sole_dispositive": p.sole_dispositive,
        "shared_dispositive": p.shared_dispositive,
        "type": p.person_type,
    }


def ingest_ownership_filing(
    session: Session,
    filing: OwnershipFiling,
    symbol: str | None,
    *,
    client: SecClient | None = None,
    xml: bytes | str | None = None,
) -> OwnershipIngestResult:
    """Fetch (or take, via `xml`) one 13D/13G and store it as one
    `ownership_filing` fact: `known_at` = SEC acceptance time, `effective_at` =
    the event date, keyed by accession (a filing listed under both the company
    and the filer is stored once). `symbol` is the company's ticker when known;
    None keeps the filing, shown by company name."""
    if xml is None:
        xml = (client or get_sec_client()).get_archive_doc(filing.url)
    doc = parse_ownership_xml(xml)
    head = doc.headline
    result = record_fact(
        session,
        kind=FactKind.OWNERSHIP_FILING,
        symbol=symbol,
        source=SOURCE_NAME,
        source_ref=filing.index_url,
        dedupe_key=make_dedupe_key(DEDUPE_PREFIX, filing.accession),
        known_at=filing.accepted_at,
        known_at_basis="source",
        effective_at=datetime.combine(doc.event_date, datetime.min.time()) if doc.event_date else None,
        payload={
            "accession": filing.accession,
            "form": filing.form,
            "schedule": doc.schedule,
            "is_amendment": doc.is_amendment,
            "amendment_no": doc.amendment_no,
            "previous_accession": doc.previous_accession,
            "filing_date": filing.filing_date.isoformat(),
            "event_date": doc.event_date.isoformat() if doc.event_date else None,
            "filer_cik": doc.filer_cik,
            "filer_name": head.name if head else None,
            "issuer_cik": doc.issuer_cik,
            "issuer_name": doc.issuer_name,
            "issuer_cusip": doc.issuer_cusip,
            "symbol": symbol,
            "class_title": doc.class_title,
            "rule": doc.rule,
            "percent": head.percent if head else None,
            "shares": head.shares if head else None,
            "persons": [_person_payload(p) for p in doc.persons],
            "purpose": doc.purpose,
            "filing_url": filing.index_url,
        },
    )
    return OwnershipIngestResult(filing.accession, result.created, result.fact, doc)


def ingested_ownership_accessions(session: Session) -> set[str]:
    """Accessions already stored (from the dedupe keys only; see the Form 4 module)."""
    keys = session.exec(select(KnownFact.dedupe_key).where(KnownFact.kind == FactKind.OWNERSHIP_FILING)).all()
    prefix = DEDUPE_PREFIX + "|"
    return {k[len(prefix) :] for k in keys if k.startswith(prefix)}


@dataclass
class OwnershipFetchResult:
    new_facts: list[KnownFact] = field(default_factory=list)
    filings_checked: int = 0
    legacy_skipped: int = 0
    unreadable: int = 0


def fetch_ownership_filings(
    session: Session,
    cik: int,
    symbol: str | None,
    *,
    client: SecClient | None = None,
    today: date | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    symbol_resolver: Callable[[str | None], str | None] | None = None,
) -> OwnershipFetchResult:
    """Ingest the 13D/13G filings listed under `cik` from the last `lookback_days`
    that are not stored yet. `symbol` is the company's ticker when `cik` is a
    company; for a filer's own list pass None and a `symbol_resolver` that turns
    each filing's issuer CIK into a ticker. Per-filing failures are logged and
    skipped; a refusal from SEC (403/429) is re-raised."""
    from app.timeutil import utcnow_naive

    client = client or get_sec_client()
    start = (today or utcnow_naive().date()) - timedelta(days=lookback_days)
    listing = list_ownership_filings(cik, since=start, client=client)
    have = ingested_ownership_accessions(session)
    result = OwnershipFetchResult(filings_checked=len(listing.filings), legacy_skipped=listing.legacy_count)
    fresh = [f for f in listing.filings if f.accession not in have][:MAX_FILINGS_PER_COMPANY]
    for filing in sorted(fresh, key=lambda f: (f.accepted_at, f.accession)):
        try:
            xml = client.get_archive_doc(filing.url)
            sym = symbol
            if sym is None and symbol_resolver is not None:
                sym = symbol_resolver(parse_ownership_xml(xml).issuer_cik)
            ingested = ingest_ownership_filing(session, filing, sym, client=client, xml=xml)
        except DataProviderError as exc:
            session.rollback()
            if "HTTP 403" in str(exc) or "HTTP 429" in str(exc):
                raise
            logger.warning("ownership filing %s unreadable: %s", filing.accession, exc)
            result.unreadable += 1
            continue
        if ingested.created and ingested.fact is not None:
            result.new_facts.append(ingested.fact)
    return result
