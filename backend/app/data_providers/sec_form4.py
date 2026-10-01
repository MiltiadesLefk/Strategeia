"""SEC Form 4 insider filings: parse them properly and store them with the time
they were actually published.

Why this exists next to `SecEdgarProvider.get_insider_activity`: that method
answers "what are insiders doing right now" with a quick regex over the latest
filings and keeps nothing. A backtest needs the opposite: every insider
transaction for years, each stamped with the moment a trader could first have
seen it. That moment is the filing's SEC ACCEPTANCE time, not the transaction
date. An insider who bought on the 22nd and filed on the 24th at 18:30 New York
time was not knowable on the 22nd, 23rd or the 24th before 18:30; a test that
used the trade date would trade on information from the future.

This module has four parts:

  1. `parse_form4_xml`: a real XML parse (the regex it replaces mis-pairs codes,
     share counts and prices once a filing has several rows, a derivative table,
     or a price that is only a footnote reference).
  2. `list_form4_filings`: the filing list for one company with each filing's
     acceptance time, from the submissions index (recent filings plus the older
     paged files).
  3. `ingest_form4_filing`: one fact per transaction row, through the dated-fact
     layer, idempotently.
  4. `backfill_insider_trades` / `fetch_new_insider_trades`: bulk and incremental
     loading on top of that.

Reading the facts back (as of any moment) lives in `app/knowledge/insider_trades.py`.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, select

from app.data_providers.base import DataProviderError
from app.data_providers.sec_client import SecClient, get_sec_client
from app.knowledge import FactKind, KnownFact, make_dedupe_key, record_fact
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

SOURCE_NAME = "sec_edgar"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"

# The ticker map covers the whole market and changes rarely; hold it in memory
# for a day rather than refetching per symbol in a multi-symbol backfill.
TICKER_MAP_TTL_SECONDS = 24 * 60 * 60

# Forms that carry insider transactions: the original and its amendment.
FORM_4 = "4"
FORM_4A = "4/A"
FORM4_FORMS = frozenset({FORM_4, FORM_4A})

# Dedupe-key prefix; the full key is "form4|<accession>|<row index>".
DEDUPE_PREFIX = "form4"

# How far back `fetch_new_insider_trades` looks when it has no accession to
# resume from, and the most filings it will open in one call.
NEW_TRADES_DEFAULT_LOOKBACK_DAYS = 7
NEW_TRADES_MAX_FILINGS = 100
# With an accession to resume from, only this much history is listed (it is
# expected to be recent; anything older that slips through dedupes harmlessly).
NEW_TRADES_RESUME_LOOKBACK_DAYS = 30

# Form 4 transaction codes (SEC's published list). Only P and S are open-market
# decisions; the rest are compensation machinery or bookkeeping, which is why
# the readers default to P.
TRANSACTION_CODE_MEANINGS: dict[str, str] = {
    "P": "Open-market or private purchase",
    "S": "Open-market or private sale",
    "V": "Transaction voluntarily reported earlier than required",
    "A": "Grant, award or other acquisition from the issuer",
    "D": "Sale or disposition back to the issuer",
    "F": "Shares withheld to pay exercise price or tax",
    "I": "Discretionary transaction (broker-directed plan)",
    "M": "Exercise or conversion of a derivative security",
    "C": "Conversion of a derivative security",
    "E": "Expiration of a short derivative position",
    "H": "Expiration (or cancellation) of a long derivative position",
    "O": "Exercise of an out-of-the-money derivative",
    "X": "Exercise of an in-the-money or at-the-money derivative",
    "G": "Bona fide gift",
    "L": "Small acquisition (Rule 16a-6)",
    "W": "Acquisition or disposition by will or the laws of descent",
    "Z": "Deposit into or withdrawal from a voting trust",
    "J": "Other acquisition or disposition (see footnote)",
    "K": "Equity swap or similar hedging transaction",
    "U": "Disposition in a change of control (tender of shares)",
}

# EDGAR's primaryDocument for a Form 4 points at the XSL-rendered HTML view
# (".../xslF345X06/form4.xml"), which holds none of the data tags; the raw XML
# sits at the same path with that directory removed.
_XSL_PREFIX_RE = re.compile(r"^xsl[^/]*/")
# "10b5-1", "10b5 - 1", "Rule 10b5-1(c)": spacing and hyphen vary by filer.
_RULE_10B5_1_RE = re.compile(r"10b5\s*-?\s*1", re.IGNORECASE)
# A document that declares entities or a DOCTYPE has no business being a Form 4
# and is how an entity-expansion attack would arrive; refuse it outright.
_FORBIDDEN_XML_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


# --------------------------------------------------------------------------
# 1. Parsing
# --------------------------------------------------------------------------


@dataclass
class ReportingOwner:
    name: str
    cik: str | None  # some old or foreign filers have none
    is_director: bool = False
    is_officer: bool = False
    is_ten_percent_owner: bool = False
    is_other: bool = False
    officer_title: str | None = None
    other_text: str | None = None

    @property
    def key(self) -> str:
        """Stable identity for "distinct insiders": the CIK, else the name."""
        return self.cik or self.name.strip().upper()

    @property
    def roles(self) -> list[str]:
        out = []
        if self.is_officer:
            out.append("officer")
        if self.is_director:
            out.append("director")
        if self.is_ten_percent_owner:
            out.append("10% owner")
        if self.is_other:
            out.append("other")
        return out


@dataclass
class Form4Transaction:
    row_index: int  # position among all transaction rows of the document, 0-based
    table: str  # "non_derivative" | "derivative"
    security_title: str | None
    transaction_date: date | None
    code: str | None
    acquired_disposed: str | None  # "A" | "D"
    shares: float | None
    price: float | None  # None when the filing gives only a footnote, never 0
    shares_after: float | None
    ownership: str | None  # "D" direct | "I" indirect
    ownership_nature: str | None  # e.g. "By Trust"
    is_10b5_1: bool = False
    plan_flag_source: str | None = None  # "checkbox" | "footnote" | None
    equity_swap: bool = False
    footnote_ids: list[str] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    underlying_title: str | None = None  # derivative rows
    underlying_shares: float | None = None
    conversion_or_exercise_price: float | None = None

    @property
    def value(self) -> float | None:
        if self.shares is None or self.price is None:
            return None
        return self.shares * self.price

    @property
    def code_meaning(self) -> str | None:
        return TRANSACTION_CODE_MEANINGS.get(self.code) if self.code else None


@dataclass
class Form4Document:
    document_type: str  # "4" | "4/A"
    period_of_report: date | None
    date_of_original_submission: date | None  # only on an amendment
    issuer_cik: str | None
    issuer_name: str | None
    issuer_symbol: str | None
    owners: list[ReportingOwner]
    aff_10b5_1: bool  # the document-level checkbox (newer filings only)
    transactions: list[Form4Transaction]
    holding_rows: int = 0  # nonDerivativeHolding / derivativeHolding: no transaction

    @property
    def is_amendment(self) -> bool:
        return self.document_type.upper().startswith("4/A") or self.date_of_original_submission is not None

    @property
    def primary_owner(self) -> ReportingOwner | None:
        return self.owners[0] if self.owners else None


def _local(tag: str) -> str:
    """Tag without any namespace, so a namespaced variant parses the same."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child(node: ET.Element | None, name: str) -> ET.Element | None:
    if node is None:
        return None
    for child in node:
        if _local(child.tag) == name:
            return child
    return None


def _children(node: ET.Element | None, name: str) -> list[ET.Element]:
    if node is None:
        return []
    return [child for child in node if _local(child.tag) == name]


def _text(node: ET.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    cleaned = node.text.strip()
    return cleaned or None


def _value_text(node: ET.Element | None, name: str) -> str | None:
    """Text of `<name><value>..</value></name>`, or of `<name>..</name>` when
    there is no value wrapper. None when absent, empty, or footnote-only."""
    child = _child(node, name)
    if child is None:
        return None
    inner = _child(child, "value")
    return _text(inner) if inner is not None else _text(child)


def _bool(raw: str | None) -> bool:
    """Form 4 writes booleans as 1/0 in older schemas and true/false in newer."""
    return raw is not None and raw.strip().lower() in ("1", "true")


def _number(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        # Some filings append a timezone offset ("2025-05-14-04:00").
        return datetime.strptime(raw.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _footnote_ids(node: ET.Element) -> list[str]:
    """Every footnote reference anywhere inside `node`, in document order, once."""
    seen: list[str] = []
    for element in node.iter():
        if _local(element.tag) == "footnoteId":
            fid = element.get("id")
            if fid and fid not in seen:
                seen.append(fid)
    return seen


def _parse_owner(node: ET.Element) -> ReportingOwner:
    ident = _child(node, "reportingOwnerId")
    rel = _child(node, "reportingOwnerRelationship")
    cik = _text(_child(ident, "rptOwnerCik"))
    return ReportingOwner(
        name=_text(_child(ident, "rptOwnerName")) or "",
        cik=cik.zfill(10) if cik and cik.isdigit() else cik,
        is_director=_bool(_text(_child(rel, "isDirector"))),
        is_officer=_bool(_text(_child(rel, "isOfficer"))),
        is_ten_percent_owner=_bool(_text(_child(rel, "isTenPercentOwner"))),
        is_other=_bool(_text(_child(rel, "isOther"))),
        officer_title=_text(_child(rel, "officerTitle")),
        other_text=_text(_child(rel, "otherText")),
    )


def _parse_transaction(
    node: ET.Element,
    table: str,
    row_index: int,
    footnotes: dict[str, str],
    document_flag: bool,
) -> Form4Transaction:
    coding = _child(node, "transactionCoding")
    amounts = _child(node, "transactionAmounts")
    after = _child(node, "postTransactionAmounts")
    nature = _child(node, "ownershipNature")
    underlying = _child(node, "underlyingSecurity")

    ids = _footnote_ids(node)
    texts = [footnotes[i] for i in ids if i in footnotes]
    # Two independent ways a filing says "this trade was under a 10b5-1 plan":
    # the document-level checkbox (added in 2023), and a footnote on the row
    # (the only way before that). Either counts; the source is recorded.
    if document_flag:
        flag_source = "checkbox"
    elif any(_RULE_10B5_1_RE.search(t) for t in texts):
        flag_source = "footnote"
    else:
        flag_source = None

    price = _number(_value_text(amounts, "transactionPricePerShare"))
    return Form4Transaction(
        row_index=row_index,
        table=table,
        security_title=_value_text(node, "securityTitle"),
        transaction_date=_date(_value_text(node, "transactionDate")),
        code=_text(_child(coding, "transactionCode")),
        acquired_disposed=_value_text(amounts, "transactionAcquiredDisposedCode"),
        shares=_number(_value_text(amounts, "transactionShares")),
        price=price,
        shares_after=_number(_value_text(after, "sharesOwnedFollowingTransaction")),
        ownership=_value_text(nature, "directOrIndirectOwnership"),
        ownership_nature=_value_text(nature, "natureOfOwnership"),
        is_10b5_1=flag_source is not None,
        plan_flag_source=flag_source,
        equity_swap=_bool(_text(_child(coding, "equitySwapInvolved"))),
        footnote_ids=ids,
        footnotes=texts,
        underlying_title=_value_text(underlying, "underlyingSecurityTitle"),
        underlying_shares=_number(_value_text(underlying, "underlyingSecurityShares")),
        conversion_or_exercise_price=_number(_value_text(node, "conversionOrExercisePrice")),
    )


def parse_form4_xml(xml: bytes | str) -> Form4Document:
    """Parse one Form 4 (or 4/A) ownershipDocument. Raises DataProviderError on
    anything that is not one, so a caller can skip a bad file and carry on."""
    raw = xml.encode("utf-8") if isinstance(xml, str) else xml
    if _FORBIDDEN_XML_RE.search(raw):
        raise DataProviderError("form 4 document declares a DOCTYPE or entity; refusing to parse it")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise DataProviderError(f"form 4 document is not valid XML: {exc}") from exc
    if _local(root.tag) != "ownershipDocument":
        raise DataProviderError(f"not an ownership document (root element {_local(root.tag)!r})")

    footnotes: dict[str, str] = {}
    for fn in _children(_child(root, "footnotes"), "footnote"):
        fid = fn.get("id")
        if fid:
            footnotes[fid] = " ".join("".join(fn.itertext()).split())

    document_flag = _bool(_text(_child(root, "aff10b5One")))
    issuer = _child(root, "issuer")
    issuer_cik = _text(_child(issuer, "issuerCik"))

    transactions: list[Form4Transaction] = []
    holdings = 0
    for table_name, table_tag, row_tag, holding_tag in (
        ("non_derivative", "nonDerivativeTable", "nonDerivativeTransaction", "nonDerivativeHolding"),
        ("derivative", "derivativeTable", "derivativeTransaction", "derivativeHolding"),
    ):
        table = _child(root, table_tag)
        for row in _children(table, row_tag):
            transactions.append(_parse_transaction(row, table_name, len(transactions), footnotes, document_flag))
        # A holding row reports what is owned, with no transaction; it must
        # never become a trade, so it is only counted.
        holdings += len(_children(table, holding_tag))

    return Form4Document(
        document_type=_text(_child(root, "documentType")) or FORM_4,
        period_of_report=_date(_text(_child(root, "periodOfReport"))),
        date_of_original_submission=_date(_text(_child(root, "dateOfOriginalSubmission"))),
        issuer_cik=issuer_cik.zfill(10) if issuer_cik and issuer_cik.isdigit() else issuer_cik,
        issuer_name=_text(_child(issuer, "issuerName")),
        issuer_symbol=_text(_child(issuer, "issuerTradingSymbol")),
        owners=[_parse_owner(o) for o in _children(root, "reportingOwner")],
        aff_10b5_1=document_flag,
        transactions=transactions,
        holding_rows=holdings,
    )


# --------------------------------------------------------------------------
# 2. The filing list, with acceptance times
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Form4Filing:
    cik: int  # the ISSUER's CIK (the archive path is keyed by it)
    accession: str  # with dashes: 0000320193-22-000076
    form: str  # "4" | "4/A"
    filing_date: date
    accepted_at: datetime  # naive UTC: when SEC accepted it, i.e. when it became public
    primary_document: str  # the raw XML path (XSL prefix removed)
    report_date: date | None = None

    @property
    def url(self) -> str:
        return ARCHIVE_URL.format(
            cik=self.cik, accession=self.accession.replace("-", ""), document=self.primary_document
        )


def parse_acceptance_time(raw: str) -> datetime:
    """`acceptanceDateTime` from the submissions index -> naive UTC.

    The index states it in UTC with a trailing Z ("2022-08-19T22:30:27.000Z" is
    18:30:27 in New York, the time EDGAR displays). No timezone conversion is
    needed or wanted here: converting it as if it were New York time would put
    every filing four or five hours into the future."""
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        # EDGAR always sends the Z. A bare time would be a format change; treat
        # it as UTC like the rest, but only after the cheap sanity above.
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _filings_from_columns(cik: int, columns: dict) -> list[Form4Filing]:
    forms = columns.get("form") or []
    accessions = columns.get("accessionNumber") or []
    filed = columns.get("filingDate") or []
    accepted = columns.get("acceptanceDateTime") or []
    documents = columns.get("primaryDocument") or []
    reports = columns.get("reportDate") or []
    out: list[Form4Filing] = []
    for i, form in enumerate(forms):
        if form not in FORM4_FORMS:
            continue
        try:
            filing_date = _date(filed[i])
            accepted_at = parse_acceptance_time(accepted[i])
            document = _XSL_PREFIX_RE.sub("", documents[i])
            accession = accessions[i]
        except (IndexError, ValueError, TypeError):
            logger.warning("skipping a malformed submissions row (form %s, index %d)", form, i)
            continue
        if filing_date is None or not document or not accession:
            continue
        out.append(
            Form4Filing(
                cik=cik,
                accession=accession,
                form=form,
                filing_date=filing_date,
                accepted_at=accepted_at,
                primary_document=document,
                report_date=_date(reports[i]) if i < len(reports) else None,
            )
        )
    return out


def list_form4_filings(
    cik: int,
    since: date | None = None,
    until: date | None = None,
    *,
    client: SecClient | None = None,
) -> list[Form4Filing]:
    """Form 4 and 4/A filings for an issuer filed within [since, until] (by
    filing date, inclusive), newest first.

    The submissions index keeps only the latest ~1000 filings in `recent`; older
    ones are in extra pages listed under `filings.files`, each with a
    filingFrom/filingTo range. A page is fetched only when its range overlaps the
    request, so a 90-day query costs exactly one request and a ten-year query
    for a busy company a few more."""
    client = client or get_sec_client()
    index = client.get_json(SUBMISSIONS_URL.format(cik=cik))
    if not isinstance(index, dict):
        raise DataProviderError(f"unexpected submissions payload for CIK {cik}")
    filings_block = index.get("filings") or {}
    found = _filings_from_columns(cik, filings_block.get("recent") or {})
    for page in filings_block.get("files") or []:
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
            found.extend(_filings_from_columns(cik, columns))

    selected = {
        f.accession: f
        for f in found
        if (since is None or f.filing_date >= since) and (until is None or f.filing_date <= until)
    }
    return sorted(selected.values(), key=lambda f: (f.accepted_at, f.accession), reverse=True)


_ticker_map: tuple[float, dict[str, int]] | None = None


def resolve_cik(symbol: str, *, client: SecClient | None = None, now: Callable[[], float] | None = None) -> int | None:
    """Ticker -> issuer CIK from SEC's ticker file, or None when the symbol is not
    an SEC registrant (a crypto pair, an index, a foreign listing) or has since
    left the file (a delisted company: pass its CIK to `list_form4_filings`
    directly)."""
    global _ticker_map
    clock = now or time.monotonic
    current = clock()
    if _ticker_map is None or current - _ticker_map[0] > TICKER_MAP_TTL_SECONDS:
        client = client or get_sec_client()
        payload = client.get_json(TICKER_MAP_URL)
        if not isinstance(payload, dict):
            raise DataProviderError("sec ticker map had an unexpected shape")
        mapping = {
            str(entry["ticker"]).upper(): int(entry["cik_str"])
            for entry in payload.values()
            if isinstance(entry, dict) and entry.get("ticker") and entry.get("cik_str") is not None
        }
        _ticker_map = (current, mapping)
    return _ticker_map[1].get(symbol.strip().upper())


# --------------------------------------------------------------------------
# 3. Ingest: one dated fact per transaction row
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class IngestResult:
    accession: str
    rows: int  # transaction rows in the filing
    created: int  # new facts written
    already_known: int  # rows that were stored before (idempotent re-run)
    new_facts: list[KnownFact] = field(default_factory=list)  # the rows created by this call


def _row_payload(
    document: Form4Document, row: Form4Transaction, filing: Form4Filing, symbol: str
) -> dict:
    owner = document.primary_owner
    return {
        "accession": filing.accession,
        "row_index": row.row_index,
        "form": filing.form,
        "filing_date": filing.filing_date.isoformat(),
        "period_of_report": document.period_of_report.isoformat() if document.period_of_report else None,
        "is_amendment": document.is_amendment,
        "date_of_original_submission": (
            document.date_of_original_submission.isoformat() if document.date_of_original_submission else None
        ),
        "issuer_cik": document.issuer_cik,
        "issuer_name": document.issuer_name,
        "issuer_symbol": document.issuer_symbol,
        "symbol": symbol.upper(),
        "table": row.table,
        "security_title": row.security_title,
        "transaction_date": row.transaction_date.isoformat() if row.transaction_date else None,
        "code": row.code,
        "code_meaning": row.code_meaning,
        "acquired_disposed": row.acquired_disposed,
        "shares": row.shares,
        "price": row.price,
        "value": row.value,
        "shares_after": row.shares_after,
        "ownership": row.ownership,
        "ownership_nature": row.ownership_nature,
        "is_10b5_1": row.is_10b5_1,
        "plan_flag_source": row.plan_flag_source,
        "equity_swap": row.equity_swap,
        "footnotes": row.footnotes,
        "underlying_title": row.underlying_title,
        "underlying_shares": row.underlying_shares,
        "conversion_or_exercise_price": row.conversion_or_exercise_price,
        # The first reporting owner is "the insider"; a joint filing's other
        # owners (a fund, a trust, a spouse) are kept in `owners`.
        "owner_name": owner.name if owner else None,
        "owner_cik": owner.cik if owner else None,
        "owner_key": owner.key if owner else None,
        "is_officer": owner.is_officer if owner else False,
        "is_director": owner.is_director if owner else False,
        "is_ten_percent_owner": owner.is_ten_percent_owner if owner else False,
        "is_other": owner.is_other if owner else False,
        "officer_title": owner.officer_title if owner else None,
        "owners": [
            {
                "name": o.name,
                "cik": o.cik,
                "is_officer": o.is_officer,
                "is_director": o.is_director,
                "is_ten_percent_owner": o.is_ten_percent_owner,
                "is_other": o.is_other,
                "officer_title": o.officer_title,
            }
            for o in document.owners
        ],
        "filing_url": filing.url,
    }


def ingest_form4_filing(
    session: Session,
    symbol: str,
    filing: Form4Filing,
    *,
    client: SecClient | None = None,
    xml: bytes | str | None = None,
) -> IngestResult:
    """Fetch (or take, via `xml`) one filing and store each transaction row as an
    `insider_trade` fact.

      known_at     = the filing's SEC acceptance time (basis "source")
      effective_at = the transaction date (what the row is ABOUT)
      dedupe key   = accession + row index, so a re-run adds nothing

    An amendment (4/A) is stored as its own set of rows under its own accession
    and later known_at; it restates the original in full, and the readers use the
    payload's period/owner/original-date to let it replace the rows it corrects
    from the moment it was accepted (and not before)."""
    if xml is None:
        xml = (client or get_sec_client()).get_archive_doc(filing.url)
    document = parse_form4_xml(xml)

    created = known = 0
    new_facts: list[KnownFact] = []
    for row in document.transactions:
        result = record_fact(
            session,
            kind=FactKind.INSIDER_TRADE,
            symbol=symbol,
            source=SOURCE_NAME,
            source_ref=filing.url,
            dedupe_key=make_dedupe_key(DEDUPE_PREFIX, filing.accession, row.row_index),
            known_at=filing.accepted_at,
            known_at_basis="source",
            effective_at=(
                datetime.combine(row.transaction_date, datetime.min.time()) if row.transaction_date else None
            ),
            payload=_row_payload(document, row, filing, symbol),
        )
        if result.created:
            created += 1
            new_facts.append(result.fact)
        else:
            known += 1
    return IngestResult(
        accession=filing.accession,
        rows=len(document.transactions),
        created=created,
        already_known=known,
        new_facts=new_facts,
    )


def ingested_accessions(session: Session, symbol: str) -> set[str]:
    """Accession numbers that already have stored rows for `symbol`.

    Writer-side bookkeeping for resuming a backfill, read from the dedupe keys
    only: it never looks at what a fact says, so it is not a reader of insider
    data and cannot leak the future into a decision. (A filing with no
    transaction rows leaves no trace and is simply re-read from the local
    document cache next time.)"""
    keys = session.exec(
        select(KnownFact.dedupe_key).where(
            KnownFact.kind == FactKind.INSIDER_TRADE, KnownFact.symbol == symbol.strip().upper()
        )
    ).all()
    prefix = DEDUPE_PREFIX + "|"
    return {k.split("|")[1] for k in keys if k.startswith(prefix) and k.count("|") >= 2}


# --------------------------------------------------------------------------
# 4. Backfill and incremental fetch
# --------------------------------------------------------------------------


@dataclass
class BackfillProgress:
    symbol: str
    filings_total: int
    filings_done: int
    facts_created: int
    skipped_existing: int
    errors: int


@dataclass
class BackfillReport:
    symbols: int = 0
    filings_seen: int = 0
    filings_ingested: int = 0
    filings_skipped_existing: int = 0
    facts_created: int = 0
    errors: list[str] = field(default_factory=list)
    unknown_symbols: list[str] = field(default_factory=list)


def backfill_insider_trades(
    session: Session,
    symbols: Iterable[str],
    since: date,
    until: date | None = None,
    *,
    client: SecClient | None = None,
    progress: Callable[[BackfillProgress], None] | None = None,
    ciks: dict[str, int] | None = None,
) -> BackfillReport:
    """Load every Form 4 filed in [since, until] for each symbol.

    Resumable and safe to re-run: filings whose accession already has stored
    rows are skipped without a request, ingestion is idempotent anyway, and a
    filing or symbol that fails is recorded in the report and skipped rather than
    aborting the run. Pacing is the shared client's rate limiter (5 requests per
    second, process-wide), so no extra sleeping is needed here. `progress` is
    called after each filing. `ciks` supplies a CIK for a symbol the ticker file
    no longer lists (a delisted company)."""
    client = client or get_sec_client()
    report = BackfillReport()
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
            filings = list_form4_filings(cik, since, until, client=client)
        except DataProviderError as exc:
            report.errors.append(f"{symbol}: {exc}")
            continue

        done = already = created_for_symbol = errors = 0
        have = ingested_accessions(session, symbol)
        report.filings_seen += len(filings)
        # Oldest first: an interrupted run leaves a contiguous history that the
        # next run extends, instead of a recent slice with a hole behind it.
        for filing in sorted(filings, key=lambda f: (f.accepted_at, f.accession)):
            if filing.accession in have:
                already += 1
                report.filings_skipped_existing += 1
            else:
                try:
                    result = ingest_form4_filing(session, symbol, filing, client=client)
                    report.filings_ingested += 1
                    report.facts_created += result.created
                    created_for_symbol += result.created
                except DataProviderError as exc:
                    session.rollback()
                    errors += 1
                    report.errors.append(f"{symbol} {filing.accession}: {exc}")
            done += 1
            if progress is not None:
                progress(
                    BackfillProgress(
                        symbol=symbol,
                        filings_total=len(filings),
                        filings_done=done,
                        facts_created=created_for_symbol,
                        skipped_existing=already,
                        errors=errors,
                    )
                )
    return report


@dataclass
class NewTradesResult:
    new_facts: list[KnownFact]  # rows stored for the first time by this call
    latest_accession: str | None  # pass back as `since_accession` next time
    filings_checked: int


def fetch_new_insider_trades(
    session: Session,
    symbol: str,
    since_accession: str | None = None,
    *,
    client: SecClient | None = None,
    cik: int | None = None,
    today: date | None = None,
) -> NewTradesResult:
    """What a watcher calls: ingest the filings for `symbol` that are newer than
    `since_accession` (the `latest_accession` of the previous call) and return
    only the rows that are new. One request for the list plus one per unseen
    filing. With no accession it looks back NEW_TRADES_DEFAULT_LOOKBACK_DAYS."""
    client = client or get_sec_client()
    symbol = symbol.strip().upper()
    cik = cik or resolve_cik(symbol, client=client)
    if cik is None:
        return NewTradesResult(new_facts=[], latest_accession=since_accession, filings_checked=0)
    lookback = NEW_TRADES_DEFAULT_LOOKBACK_DAYS if since_accession is None else NEW_TRADES_RESUME_LOOKBACK_DAYS
    start = (today or utcnow_naive().date()) - timedelta(days=lookback)
    listed = list_form4_filings(cik, since=start, client=client)
    # `listed` is newest first. Stop at the accession we already handled.
    fresh: list[Form4Filing] = []
    for filing in listed[:NEW_TRADES_MAX_FILINGS]:
        if since_accession is not None and filing.accession == since_accession:
            break
        fresh.append(filing)

    have = ingested_accessions(session, symbol)
    new_facts: list[KnownFact] = []
    for filing in sorted(fresh, key=lambda f: (f.accepted_at, f.accession)):
        if filing.accession in have:
            continue
        try:
            result = ingest_form4_filing(session, symbol, filing, client=client)
        except DataProviderError as exc:
            session.rollback()
            logger.warning("new insider filing %s for %s unreadable: %s", filing.accession, symbol, exc)
            continue
        new_facts.extend(result.new_facts)
    latest = listed[0].accession if listed else since_accession
    return NewTradesResult(new_facts=new_facts, latest_accession=latest, filings_checked=len(fresh))
