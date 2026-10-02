"""U.S. House of Representatives stock-trade reports (Periodic Transaction Reports).

Members of the House must report every stock trade they (or a spouse or dependent
child) make, within 45 days of the trade. The Clerk of the House publishes those
reports as PDFs, plus one index file per year. This module:

  1. reads the yearly index (a zip holding an XML list of every filing: who,
     what kind, the filing date and a document id);
  2. downloads the trade-report PDFs (type "P" in the index) and keeps them on
     disk, because a published report never changes;
  3. reads the PDF's table with a pure-Python library (pypdf): one row per trade
     with the owner (member, spouse, dependent child, joint), the asset and its
     ticker, the kind of trade, the trade and notification dates and the AMOUNT
     RANGE;
  4. stores each trade as a dated fact (`app.knowledge`), whose `known_at` is the
     FILING date and whose `effective_at` is the trade date.

What the data can and cannot say (read before using it):

  * Amounts are RANGES the form prescribes ($1,001-$15,000, $15,001-$50,000, ...,
    "Over $50,000,000"), never a dollar figure. This module keeps the low and the
    high end and never turns them into one number.
  * A report may be filed up to 45 days after the trade (some are later still), so
    the trade is old news by the time anyone can see it. That is why `known_at` is
    the filing date: a backtest that used the trade date would trade on
    information that did not exist yet. The index gives a date but no time, so the
    end of that day in New York is used (the latest the report can have appeared).
  * Spouse and dependent-child trades are included and marked; they are not
    necessarily the member's own decisions.
  * Some PDFs are scanned paper forms with no text. They are recorded as
    "unreadable" and never guessed at; reading them would need OCR, which this
    module deliberately does not do.
  * A row with no ticker (a bond, a private fund, a trust) is kept with
    `symbol=None`. Only stocks (asset code ST) and stock options (OP) carry a
    symbol, and only stocks are ever counted by the readers.
  * The Senate publishes its own reports, but its site refuses scripted access, so
    nothing here covers the Senate. `SENATE_SOURCE_NOTE` says so, and a
    third-party Senate feed could be added next to this module the same way.

How the PDF is read: the Clerk's reports are produced by one program, so every
page has the same table: columns at fixed horizontal positions with the header
names above them, body text in one font size and each row's footer (filing status,
"subholding of", description) in a smaller one. Instead of flattening the page to
text (which interleaves the columns), each piece of text is read together with its
position and sorted into its column by the page's own header. A row ends where its
footer begins ("Filing Status: ..."), which also handles a row that is split over
a page break. Whatever cannot be read this way is counted (`skipped_blocks`), the
filing is marked "partial", and nothing is guessed.

The same code is used by the watcher (`app.watchers.house_watcher`), the refresh
button and `scripts/backfill_house.py`.
"""

from __future__ import annotations

import io
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path

from sqlmodel import Session, select

from app.config import get_infra_settings
from app.data_providers import health
from app.data_providers.base import DataProviderError
from app.data_providers.sec_client import DocCache, RateLimiter, SecClient
from app.data_providers.sec_edgar_provider import _headers
from app.knowledge import FactKind, KnownFact, end_of_local_day_utc, make_dedupe_key, record_fact
from app.knowledge.congress_trades import CONGRESS_FILING_KIND, member_key
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

SOURCE_NAME = "house_clerk"
HEALTH_NAME = "house_clerk"
INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
PTR_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
SENATE_SOURCE_NOTE = (
    "The Senate's disclosure site blocks scripted access, so Senate trades are not available here."
)

# Index "FilingType" of a Periodic Transaction Report (the other letters are
# annual reports, candidate reports, extensions, withdrawals ...).
PTR_FILING_TYPE = "P"

# The Clerk's server is a public service with no stated limit; one request a
# second is plainly polite for the few dozen files a poll or backfill needs.
MAX_REQUESTS_PER_SECOND = 1.0
# The yearly index changes as filings arrive, so it is held in memory briefly
# (a poll every few hours, plus a refresh click, should not download it each time).
INDEX_CACHE_SECONDS = 60 * 60
# The index zip is about 100 KB; anything this much larger is not the index.
MAX_INDEX_BYTES = 20 * 1024 * 1024
# A report that parses to more rows than this is read as a layout the parser
# does not understand rather than a real filing (the biggest real ones have a few hundred).
MAX_ROWS_PER_FILING = 2000
DOC_CACHE_DIRNAME = "house_ptr"

# Report year as the index uses it also names the PDF folder. A report filed in
# January for December trades still sits in the previous year's index.

OWNER_NAMES = {"": "self", "SP": "spouse", "DC": "dependent child", "JT": "joint"}
# Transaction type codes on the form.
TYPE_CODES = {"P": "purchase", "S": "sale", "S (partial)": "partial sale", "E": "exchange"}
BUY_CODES = frozenset({"P"})
SELL_CODES = frozenset({"S", "S (partial)"})
# Asset type codes whose ticker is stored as the fact's symbol: stocks (the
# form's "ST" includes ETFs) and stock options. Everything else (bonds, funds,
# trusts, private holdings) keeps `symbol=None` so it can never be scored.
SYMBOL_ASSET_CODES = frozenset({"ST", "OP"})

_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_TYPE_RE = re.compile(r"^(P|S|E|S \(partial\))$")
_RANGE_RE = re.compile(r"^\$([\d,]+)\s*-\s*\$([\d,]+)$")
_OVER_RE = re.compile(r"^Over\s+\$([\d,]+)$", re.IGNORECASE)
_PLUS_RE = re.compile(r"^\$([\d,]+)\s*\+$")
_ASSET_CODE_RE = re.compile(r"\[([A-Z]{2})\]\s*$")
_TICKER_RE = re.compile(r"\((?:Symbol:\s*)?([A-Z][A-Z0-9]{0,5}(?:[.\-][A-Z]{1,2})?)\)\s*$")
_FORBIDDEN_XML_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)

# Text chunks within this many points of a column's header start belong to it
# (a right-aligned header is a few points off the body text's start).
COLUMN_TOLERANCE = 6.0
# A chunk this much smaller than the body text is a row footer.
FOOTER_SIZE_GAP = 0.3
# Fallback column starts (the layout the Clerk's reports use), only for a page
# whose header cannot be found.
DEFAULT_COLUMNS = {"owner": 57.6, "asset": 109.6, "type": 318.6, "tdate": 404.6, "ndate": 477.6, "amount": 563.6, "gains": 670.8}
_COLUMN_ORDER = ("owner", "asset", "type", "tdate", "ndate", "amount", "gains")
MAX_NOTES_LENGTH = 300


# --------------------------------------------------------------------------
# The yearly index
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HouseFiling:
    """One line of the Clerk's index."""

    year: int  # the index year; it also names the PDF folder
    doc_id: str
    filing_type: str  # "P" = periodic transaction report
    filing_date: date | None
    first: str
    last: str
    prefix: str
    suffix: str
    state_district: str  # e.g. "CA11"

    @property
    def member_name(self) -> str:
        """"Nancy Pelosi", "Thomas H. Kean Jr": no honorific, first name first."""
        parts = [self.first.strip(), self.last.strip(), self.suffix.strip()]
        return " ".join(p for p in parts if p)

    @property
    def url(self) -> str:
        return PTR_URL.format(year=self.year, doc_id=self.doc_id)


def _parse_us_date(raw: str | None) -> date | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%m/%d/%Y").date()
    except ValueError:
        return None


def parse_index_xml(xml: bytes, year: int) -> list[HouseFiling]:
    """The Clerk's `{year}FD.xml` -> filings. Entries without a document id are
    skipped. A DOCTYPE or ENTITY declaration is refused (this is a third party's
    XML)."""
    if _FORBIDDEN_XML_RE.search(xml):
        raise DataProviderError("house index contained a DOCTYPE or ENTITY declaration; refused")
    try:
        root = ET.fromstring(xml.lstrip(b"\xef\xbb\xbf"))
    except ET.ParseError as exc:
        raise DataProviderError(f"house index was not valid XML: {exc}") from exc
    out: list[HouseFiling] = []
    for node in root.iter("Member"):
        doc_id = (node.findtext("DocID") or "").strip()
        if not doc_id.isdigit():
            continue
        out.append(
            HouseFiling(
                year=int((node.findtext("Year") or "").strip() or year),
                doc_id=doc_id,
                filing_type=(node.findtext("FilingType") or "").strip().upper(),
                filing_date=_parse_us_date(node.findtext("FilingDate")),
                first=(node.findtext("First") or "").strip(),
                last=(node.findtext("Last") or "").strip(),
                prefix=(node.findtext("Prefix") or "").strip(),
                suffix=(node.findtext("Suffix") or "").strip(),
                state_district=(node.findtext("StateDst") or "").strip().upper(),
            )
        )
    return out


def parse_index_zip(body: bytes, year: int) -> list[HouseFiling]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as exc:
        raise DataProviderError(f"house index for {year} was not a zip file") from exc
    with archive:
        name = next((n for n in archive.namelist() if n.lower().endswith(".xml")), None)
        if name is None:
            raise DataProviderError(f"house index for {year} held no XML file")
        if archive.getinfo(name).file_size > MAX_INDEX_BYTES:
            raise DataProviderError(f"house index for {year} is unexpectedly large; refused")
        return parse_index_xml(archive.read(name), year)


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------


class HouseClient(SecClient):
    """The same polite, retrying, disk-caching client the SEC code uses (a rate
    limiter shared by every thread, backoff on 429/5xx, a timeout on each call),
    pointed at the Clerk's site and listed under its own name in the Data sources
    health. PDFs go through `get_archive_doc`, so a published report is downloaded
    once."""

    def get_bytes(self, url: str) -> bytes:
        with health.track(HEALTH_NAME, "get"):
            return self._get_bytes_with_retries(url)


_client: HouseClient | None = None
_client_lock = threading.Lock()


def default_doc_cache_dir() -> Path:
    return get_infra_settings().settings_file.parent / DOC_CACHE_DIRNAME


def get_house_client() -> HouseClient:
    """The process-wide client (one instance, so one rate limiter)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = HouseClient(
                limiter=RateLimiter(rate=MAX_REQUESTS_PER_SECOND),
                doc_cache=DocCache(default_doc_cache_dir()),
                headers=_headers,
            )
        return _client


# Reports filed in January and February for the end of the year before still sit
# in the previous year's index, so those months look at both years.
PREVIOUS_YEAR_UNTIL_MONTH = 2


def index_years(today: date) -> list[int]:
    """The index years a poll or refresh should read for `today`, newest first."""
    years = [today.year]
    if today.month <= PREVIOUS_YEAR_UNTIL_MONTH:
        years.append(today.year - 1)
    return years


_index_cache: dict[int, tuple[float, list[HouseFiling]]] = {}
_index_lock = threading.Lock()


def clear_index_cache() -> None:
    with _index_lock:
        _index_cache.clear()


def fetch_index(year: int, *, client: SecClient | None = None, now: Callable[[], float] = time.monotonic) -> list[HouseFiling]:
    """Every filing of every type in `year`'s index (held in memory for
    INDEX_CACHE_SECONDS)."""
    with _index_lock:
        hit = _index_cache.get(year)
        if hit is not None and now() - hit[0] < INDEX_CACHE_SECONDS:
            return hit[1]
    client = client or get_house_client()
    filings = parse_index_zip(client.get_bytes(INDEX_URL.format(year=year)), year)
    with _index_lock:
        _index_cache[year] = (now(), filings)
    return filings


def list_ptr_filings(
    year: int,
    since: date | None = None,
    until: date | None = None,
    *,
    client: SecClient | None = None,
) -> list[HouseFiling]:
    """Trade reports in `year`'s index filed in [since, until], oldest first. A
    report whose index line has no readable filing date is left out: without it
    there is no honest known_at."""
    out = []
    for filing in fetch_index(year, client=client):
        if filing.filing_type != PTR_FILING_TYPE or filing.filing_date is None:
            continue
        if since is not None and filing.filing_date < since:
            continue
        if until is not None and filing.filing_date > until:
            continue
        out.append(filing)
    out.sort(key=lambda f: (f.filing_date, f.doc_id))
    return out


# --------------------------------------------------------------------------
# Reading a trade-report PDF
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PtrRow:
    row_index: int
    owner_code: str  # "" (the member), "SP", "DC" or "JT"
    asset: str  # the asset name as printed, without the type code
    asset_type: str | None  # the form's two-letter code, e.g. "ST"
    ticker: str | None
    type_code: str  # "P", "S", "S (partial)" or "E"
    trade_date: date
    notification_date: date
    amount_low: int
    amount_high: int | None  # None for an open-ended top range ("Over $50,000,000")
    amount_text: str
    filing_status: str | None
    notes: str  # footer text: "subholding of", description, comments

    @property
    def owner(self) -> str:
        return OWNER_NAMES.get(self.owner_code, self.owner_code.lower())

    @property
    def side(self) -> str:
        return "buy" if self.type_code in BUY_CODES else "sell" if self.type_code in SELL_CODES else "other"

    @property
    def symbol(self) -> str | None:
        return self.ticker if self.ticker and self.asset_type in SYMBOL_ASSET_CODES else None


@dataclass
class ParsedPtr:
    """What one PDF yielded. `status`: "ok" (every row read), "partial" (some
    blocks could not be read: see `skipped_blocks`) or "unreadable" (nothing
    usable: a scanned image, a broken file or a layout this reader does not know)."""

    status: str
    reason: str | None = None
    name_in_pdf: str | None = None
    state_district_in_pdf: str | None = None
    rows: list[PtrRow] = field(default_factory=list)
    skipped_blocks: int = 0


def parse_amount(text: str) -> tuple[int, int | None] | None:
    """The form's amount range -> (low, high). "Over $50,000,000" -> (50000000,
    None): an open top end is kept open, never given a made-up ceiling."""
    cleaned = " ".join(text.split())
    for pattern, kind in ((_RANGE_RE, "range"), (_OVER_RE, "over"), (_PLUS_RE, "over")):
        match = pattern.match(cleaned)
        if match:
            low = int(match.group(1).replace(",", ""))
            if kind == "range":
                high = int(match.group(2).replace(",", ""))
                return (low, high) if high >= low else None
            return low, None
    return None


def range_midpoint(low: int | None, high: int | None) -> float | None:
    """Middle of a range, or None when the range has no upper end."""
    if low is None or high is None:
        return None
    return (low + high) / 2


def split_asset(text: str) -> tuple[str, str | None, str | None]:
    """"Apple Inc. - Common Stock (AAPL) [ST]" -> (name, asset code, ticker).
    Funds print the ticker as "(Symbol: ABCDX)"; both spellings are read."""
    cleaned = " ".join(text.split())
    code = None
    match = _ASSET_CODE_RE.search(cleaned)
    if match:
        code = match.group(1)
        cleaned = cleaned[: match.start()].strip()
    ticker = None
    ticker_match = _TICKER_RE.search(cleaned)
    if ticker_match:
        ticker = ticker_match.group(1)
    return cleaned, code, ticker


@dataclass
class _Chunk:
    page: int
    x: float
    y: float
    size: float
    text: str


def _extract_chunks(reader) -> list[_Chunk]:
    chunks: list[_Chunk] = []
    for page_number, page in enumerate(reader.pages):
        page_chunks: list[_Chunk] = []

        def visit(text, cm, tm, font_dict, font_size, _page=page_number, _out=page_chunks):
            # The form's small-cap headings come through padded with NUL bytes.
            cleaned = text.replace("\x00", "")
            if cleaned.strip():
                _out.append(_Chunk(_page, float(tm[4]), float(tm[5]), float(font_size or 0.0), cleaned.strip()))

        page.extract_text(visitor_text=visit)
        page_chunks.sort(key=lambda c: (round(c.y, 1), c.x))
        chunks.extend(page_chunks)
    return chunks


def _page_columns(page_chunks: list[_Chunk]) -> tuple[dict[str, float], float] | None:
    """Column start positions and the y below which the table body begins, read
    from this page's header, or None when the page has no table header."""
    by_text: dict[str, _Chunk] = {}
    for chunk in page_chunks:
        by_text.setdefault(chunk.text, chunk)
    transaction = by_text.get("Transaction")
    owner, asset, notification, amount = (by_text.get(k) for k in ("Owner", "Asset", "Notification", "Amount"))
    if not (transaction and owner and asset and notification and amount):
        return None
    tdate = next((c for c in page_chunks if c.text == "Date" and abs(c.y - transaction.y) < 3), None)
    gains = by_text.get("Cap.")
    columns = {
        "owner": owner.x,
        "asset": asset.x,
        "type": transaction.x,
        "tdate": tdate.x if tdate else DEFAULT_COLUMNS["tdate"] + (transaction.x - DEFAULT_COLUMNS["type"]),
        "ndate": notification.x,
        "amount": amount.x,
        "gains": gains.x if gains else DEFAULT_COLUMNS["gains"] + (amount.x - DEFAULT_COLUMNS["amount"]),
    }
    bottom = max((c.y for c in page_chunks if c.x >= transaction.x - 1 and c.y >= transaction.y and c.y <= transaction.y + 60 and c.size >= transaction.size - 0.1), default=transaction.y)
    return columns, bottom


def _column_of(x: float, columns: dict[str, float]) -> str | None:
    """The rightmost column that starts at or before this x (with a little give);
    None for text left of the owner column (the unused "ID" column)."""
    chosen = None
    for name in _COLUMN_ORDER:
        if columns[name] <= x + COLUMN_TOLERANCE:
            chosen = name
    if chosen is not None and x < columns["owner"] - COLUMN_TOLERANCE:
        return None
    return chosen


def parse_ptr_pdf(pdf: bytes) -> ParsedPtr:
    """Read a trade-report PDF. Never raises: a file that cannot be read is
    returned as status "unreadable" with the reason."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf))
        chunks = _extract_chunks(reader)
    except Exception as exc:  # pypdf raises many types on damaged files
        return ParsedPtr(status="unreadable", reason=f"not a readable PDF ({type(exc).__name__})")
    if not chunks:
        return ParsedPtr(status="unreadable", reason="no text layer: a scanned image (not read, no OCR)")

    name_in_pdf = state_in_pdf = None
    for i, chunk in enumerate(chunks[:-1]):
        nxt = chunks[i + 1]
        if chunk.text == "Name:" and name_in_pdf is None:
            name_in_pdf = nxt.text
        if chunk.text == "State/District:" and state_in_pdf is None:
            state_in_pdf = nxt.text

    # Body text is the size of the text in the date columns; footers are smaller.
    date_sizes = Counter(round(c.size, 1) for c in chunks if _DATE_RE.match(c.text))
    if not date_sizes:
        return ParsedPtr(
            status="unreadable",
            reason="no transaction table found (an unfamiliar layout)",
            name_in_pdf=name_in_pdf,
            state_district_in_pdf=state_in_pdf,
        )
    body_size = date_sizes.most_common(1)[0][0]

    rows: list[PtrRow] = []
    skipped = 0
    columns = dict(DEFAULT_COLUMNS)
    body: dict[str, list[str]] = {}
    in_footer = False
    last_footer: list[str] = []
    finished = False

    def finish_block() -> None:
        nonlocal body, skipped
        if not body:
            return
        block, body = body, {}
        row = _build_row(len(rows), block)
        if row is None:
            skipped += 1
        else:
            rows.append(row)

    pages = sorted({c.page for c in chunks})
    for page in pages:
        if finished:
            break
        page_chunks = [c for c in chunks if c.page == page]
        found = _page_columns(page_chunks)
        top = -1.0
        if found is not None:
            columns, top = found
        for chunk in page_chunks:
            if chunk.y <= top:
                continue
            if chunk.text.startswith("* For the complete list"):
                finished = True
                break
            if chunk.text.startswith("F S:") or chunk.text == "F S:":
                finish_block()
                in_footer = True
                last_footer = [chunk.text]
                continue
            is_body = chunk.size >= body_size - FOOTER_SIZE_GAP
            if is_body:
                if in_footer:
                    _attach_footer(rows, last_footer)
                    in_footer = False
                    last_footer = []
                column = _column_of(chunk.x, columns)
                if column is not None and column != "gains":
                    body.setdefault(column, []).append(chunk.text)
            elif in_footer:
                last_footer.append(chunk.text)
    finish_block()
    if in_footer:
        _attach_footer(rows, last_footer)

    if not rows:
        return ParsedPtr(
            status="unreadable",
            reason="a transaction table was found but no row could be read",
            name_in_pdf=name_in_pdf,
            state_district_in_pdf=state_in_pdf,
            skipped_blocks=skipped,
        )
    if len(rows) > MAX_ROWS_PER_FILING:
        return ParsedPtr(status="unreadable", reason=f"more than {MAX_ROWS_PER_FILING} rows: layout not understood")
    return ParsedPtr(
        status="partial" if skipped else "ok",
        reason=f"{skipped} block(s) of the table could not be read" if skipped else None,
        name_in_pdf=name_in_pdf,
        state_district_in_pdf=state_in_pdf,
        rows=rows,
        skipped_blocks=skipped,
    )


def _attach_footer(rows: list[PtrRow], footer: list[str]) -> None:
    """Footer text belongs to the row that ended just before it."""
    if not rows or not footer:
        return
    text = " ".join(" ".join(footer).split())
    status = None
    match = re.match(r"F S:\s*(\w+)", text)
    if match:
        status = match.group(1)
        text = text[match.end():].strip()
    last = rows[-1]
    rows[-1] = replace(last, filing_status=status, notes=text[:MAX_NOTES_LENGTH])


def _build_row(index: int, block: dict[str, list[str]]) -> PtrRow | None:
    owner_code = " ".join(block.get("owner", [])).strip().upper()
    if owner_code not in OWNER_NAMES:
        return None
    type_code = " ".join(" ".join(block.get("type", [])).split())
    tdate = _parse_us_date(" ".join(block.get("tdate", [])))
    ndate = _parse_us_date(" ".join(block.get("ndate", [])))
    amount = parse_amount(" ".join(block.get("amount", [])))
    asset_text = " ".join(block.get("asset", []))
    if type_code not in TYPE_CODES or tdate is None or ndate is None or amount is None or not asset_text.strip():
        return None
    name, code, ticker = split_asset(asset_text)
    return PtrRow(
        row_index=index,
        owner_code=owner_code,
        asset=name,
        asset_type=code,
        ticker=ticker,
        type_code=type_code,
        trade_date=tdate,
        notification_date=ndate,
        amount_low=amount[0],
        amount_high=amount[1],
        amount_text=" ".join(" ".join(block.get("amount", [])).split()),
        filing_status=None,
        notes="",
    )


# --------------------------------------------------------------------------
# Storing
# --------------------------------------------------------------------------


@dataclass
class IngestResult:
    doc_id: str
    status: str  # "ok" | "partial" | "unreadable"
    rows_created: int
    rows_total: int
    new_trade_facts: list[KnownFact] = field(default_factory=list)
    reason: str | None = None


def filing_known_at(filing_date: date, now: datetime | None = None) -> datetime:
    """When a report filed on `filing_date` first counts as public: the end of
    that day in New York (the index gives no time), but never later than now (a
    report cannot be known before we fetched it)."""
    end = end_of_local_day_utc(filing_date)
    current = now or utcnow_naive()
    return min(end, current)


def ingest_ptr(
    session: Session,
    filing: HouseFiling,
    pdf: bytes | None = None,
    *,
    client: SecClient | None = None,
) -> IngestResult:
    """Download (unless `pdf` is given), read and store one trade report: one
    fact per readable row plus one filing record (status, row count, reason), so a
    scanned report is remembered as unreadable instead of being fetched again.
    Idempotent: re-running adds nothing. A download failure raises
    DataProviderError and stores nothing (it is retried next time)."""
    if filing.filing_date is None:
        raise ValueError("a filing without a date has no honest known_at")
    if pdf is None:
        pdf = (client or get_house_client()).get_archive_doc(filing.url)
    parsed = parse_ptr_pdf(pdf)
    known_at = filing_known_at(filing.filing_date)
    member = filing.member_name or parsed.name_in_pdf or "Unknown member"
    district = filing.state_district or (parsed.state_district_in_pdf or "")

    created: list[KnownFact] = []
    for row in parsed.rows:
        payload = {
            "doc_id": filing.doc_id,
            "row_index": row.row_index,
            "member": member,
            "member_key": member_key(member),
            "state_district": district or None,
            "owner": row.owner,
            "owner_code": row.owner_code,
            "asset": row.asset,
            "asset_type": row.asset_type,
            "ticker": row.ticker,
            "type_code": row.type_code,
            "type": TYPE_CODES[row.type_code],
            "side": row.side,
            "amount_low": row.amount_low,
            "amount_high": row.amount_high,
            "amount_text": row.amount_text,
            "trade_date": row.trade_date.isoformat(),
            "notification_date": row.notification_date.isoformat(),
            "filed_date": filing.filing_date.isoformat(),
            "filing_year": filing.year,
            "filing_url": filing.url,
            "filing_status": row.filing_status,
            "notes": row.notes,
        }
        result = record_fact(
            session,
            kind=FactKind.CONGRESS_TRADE,
            symbol=row.symbol,
            source=SOURCE_NAME,
            source_ref=filing.url,
            dedupe_key=make_dedupe_key("house", filing.doc_id, row.row_index),
            known_at=known_at,
            known_at_basis="derived",
            effective_at=datetime.combine(row.trade_date, datetime.min.time()),
            payload=payload,
            commit=False,
        )
        if result.created:
            created.append(result.fact)
    record_fact(
        session,
        kind=CONGRESS_FILING_KIND,
        symbol=None,
        source=SOURCE_NAME,
        source_ref=filing.url,
        dedupe_key=make_dedupe_key("house", filing.doc_id),
        known_at=known_at,
        known_at_basis="derived",
        effective_at=datetime.combine(filing.filing_date, datetime.min.time()),
        payload={
            "doc_id": filing.doc_id,
            "member": member,
            "member_key": member_key(member),
            "state_district": district or None,
            "filed_date": filing.filing_date.isoformat(),
            "filing_year": filing.year,
            "filing_url": filing.url,
            "status": parsed.status,
            "reason": parsed.reason,
            "rows": len(parsed.rows),
            "skipped_blocks": parsed.skipped_blocks,
        },
        commit=False,
    )
    session.commit()
    for fact in created:
        session.refresh(fact)
    return IngestResult(
        doc_id=filing.doc_id,
        status=parsed.status,
        rows_created=len(created),
        rows_total=len(parsed.rows),
        new_trade_facts=created,
        reason=parsed.reason,
    )


def ingested_doc_ids(session: Session) -> set[str]:
    """Document ids already stored (a report is recorded even when it could not
    be read). Writer-side bookkeeping from dedupe keys only; it never looks at what
    a fact says, so it cannot leak the future into a decision."""
    keys = session.exec(select(KnownFact.dedupe_key).where(KnownFact.kind == CONGRESS_FILING_KIND)).all()
    return {k.split("|")[1] for k in keys if k.startswith("house|") and k.count("|") == 1}


@dataclass
class HouseBackfillProgress:
    filings_total: int
    filings_done: int
    rows_created: int
    skipped_existing: int
    errors: int


@dataclass
class HouseBackfillReport:
    filings_listed: int = 0
    filings_ingested: int = 0
    filings_skipped_existing: int = 0
    filings_remaining: int = 0
    rows_created: int = 0
    unreadable: int = 0
    partial: int = 0
    errors: list[str] = field(default_factory=list)


def backfill_house(
    session: Session,
    year: int,
    *,
    since: date | None = None,
    until: date | None = None,
    max_filings: int | None = None,
    client: SecClient | None = None,
    progress: Callable[[HouseBackfillProgress], None] | None = None,
) -> HouseBackfillReport:
    """Load every trade report of `year`'s index filed in [since, until].

    Resumable and safe to re-run: reports already stored (read or not) are skipped
    without a request, and the PDFs themselves are cached on disk. A report that
    fails to download is recorded in the report and skipped, then retried next
    run. Oldest first, so an interrupted run leaves a contiguous history.
    `max_filings` bounds how many NEW reports are fetched in one call (the
    refresh button's bound); `filings_remaining` says how many are left."""
    client = client or get_house_client()
    report = HouseBackfillReport()
    filings = list_ptr_filings(year, since, until, client=client)
    report.filings_listed = len(filings)
    have = ingested_doc_ids(session)
    todo = [f for f in filings if f.doc_id not in have]
    report.filings_skipped_existing = len(filings) - len(todo)
    batch = todo if max_filings is None else todo[:max_filings]
    report.filings_remaining = len(todo) - len(batch)
    done = 0
    for filing in batch:
        try:
            result = ingest_ptr(session, filing, client=client)
        except DataProviderError as exc:
            session.rollback()
            report.errors.append(f"{filing.doc_id}: {exc}")
        else:
            report.filings_ingested += 1
            report.rows_created += result.rows_created
            report.unreadable += result.status == "unreadable"
            report.partial += result.status == "partial"
        done += 1
        if progress is not None:
            progress(
                HouseBackfillProgress(
                    filings_total=len(batch),
                    filings_done=done,
                    rows_created=report.rows_created,
                    skipped_existing=report.filings_skipped_existing,
                    errors=len(report.errors),
                )
            )
    return report


__all__ = [
    "HouseFiling",
    "ParsedPtr",
    "PtrRow",
    "backfill_house",
    "fetch_index",
    "get_house_client",
    "ingest_ptr",
    "list_ptr_filings",
    "parse_amount",
    "parse_index_zip",
    "parse_ptr_pdf",
    "range_midpoint",
    "split_asset",
]
