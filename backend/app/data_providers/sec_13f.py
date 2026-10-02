"""SEC Form 13F-HR: what large funds hold, as of each quarter end, stored with the
time the filing was actually published.

Who files: an institutional manager with over $100 million of US-listed
securities reports its holdings within 45 days of every quarter end. Three
properties shape everything below, and the app says them out loud wherever the
numbers are shown:

  * LONG positions only. Shorts are not reported. Put and call options appear as
    their own rows (with the underlying share count), and a fund's hedges are
    invisible.
  * A snapshot of the quarter's LAST day, published up to 45 days later. A fund
    can have bought and sold the stock inside the quarter without it ever
    showing. There are no trade dates.
  * A manager can ask for confidential treatment of a position, so a filing may
    be incomplete on purpose.

This module has the same shape as `sec_form4`:

  1. parsing: `parse_13f_cover` (period, manager, amendment type) and
     `parse_information_table` (one row per line of the filing's table), with
     `combine_holdings` merging the several rows a fund may file for one security;
  2. `list_13f_filings`: the filing list for one manager with each filing's
     acceptance time;
  3. `ingest_13f_filing`: one `fund_holding` fact per security plus one
     `fund_filing` fact per filing, through the dated-fact layer, idempotently;
  4. `backfill_13f` / `fetch_new_13f`: bulk and incremental loading.

Each fact's `known_at` is the filing's SEC acceptance time and `effective_at` is
the period end it describes. A backtest therefore sees a June 30 position only
from mid-August, which is when anyone could first have seen it.

Value units: the filing's `value` column was in thousands of dollars until 2023
and is meant to be whole dollars since, but some managers still report
thousands. `infer_value_unit` decides per filing and says how it decided.

CUSIP to ticker: there is no free official map. `IssuerMatcher` matches the
issuer's name, exactly after a fixed normalisation, to the names of known
companies (the watchlist catalogue first, then SEC's own company list), and a
short table of CUSIPs verified by hand covers share classes whose names are
identical. A holding that does not match exactly one company keeps
`symbol=None`: it is stored and shown by name, and never given a guessed ticker.

The SEC also publishes a quarterly bulk file of every manager's 13F data
(sec.gov/data-research/sec-markets-data/form-13f-data-sets). Loading that would
cover the whole market in one download; it is a possible later source, not used
here.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
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
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

SOURCE_NAME = "sec_edgar"
ARCHIVE_DIR_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}"
INDEX_JSON_URL = ARCHIVE_DIR_URL + "/index.json"
DOCUMENT_URL = ARCHIVE_DIR_URL + "/{document}"

FORM_13F_HR = "13F-HR"
FORM_13F_HR_A = "13F-HR/A"
FORM_13F_NT = "13F-NT"
FORM_13F_NT_A = "13F-NT/A"
HOLDINGS_FORMS = frozenset({FORM_13F_HR, FORM_13F_HR_A})
NOTICE_FORMS = frozenset({FORM_13F_NT, FORM_13F_NT_A})
ALL_13F_FORMS = HOLDINGS_FORMS | NOTICE_FORMS

# Amendment kinds named in a 13F-HR/A's cover page. A restatement replaces the
# original filing for that quarter; "new holdings" adds lines the original left out.
AMENDMENT_RESTATEMENT = "RESTATEMENT"
AMENDMENT_NEW_HOLDINGS = "NEW HOLDINGS"

# Dedupe-key prefixes: "13f|<accession>|<cusip>|<put/call>|<SH/PRN>" and "13f_filing|<accession>".
HOLDING_PREFIX = "13f"
FILING_PREFIX = "13f_filing"

# Until this period end the value column was in thousands of dollars (the SEC
# changed the form to whole dollars for periods after 2022).
WHOLE_DOLLAR_FROM_PERIOD = date(2023, 1, 1)
# A portfolio of listed securities does not have a median price per share under
# a dollar. Read in whole dollars, a filing that reports thousands comes out at
# a tenth of a cent to a few tenths of a dollar (price / 1000), so a median
# below this means the filing is in thousands even though the period says dollars.
MIN_PLAUSIBLE_MEDIAN_PRICE = 1.0

# Filings are looked for this far back by default (a year of quarters).
DEFAULT_BACKFILL_QUARTERS = 4
# Without a filing to resume from, a watcher looks this far back.
NEW_FILINGS_LOOKBACK_DAYS = 120
NEW_FILINGS_MAX = 12

_XSL_PREFIX_RE = re.compile(r"^xsl[^/]*/")
_FORBIDDEN_XML_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


@dataclass(frozen=True)
class StarterFund:
    cik: int
    name: str


# Starting list: widely watched, concentrated managers whose filings are short and
# readable. Each CIK was checked against EDGAR's submissions index (name and the
# form 13F-HR/13F-NT filings), 2026-10. Editable: the setting
# `smart_money_followed_funds` (CIK numbers) replaces it.
STARTER_FUNDS: tuple[StarterFund, ...] = (
    StarterFund(1067983, "Berkshire Hathaway"),
    StarterFund(1336528, "Pershing Square Capital Management"),
    StarterFund(1649339, "Scion Asset Management"),
    StarterFund(1536411, "Duquesne Family Office"),
    StarterFund(1656456, "Appaloosa"),
    StarterFund(1350694, "Bridgewater Associates"),
)


def starter_fund_ciks() -> list[int]:
    return [f.cik for f in STARTER_FUNDS]


def starter_fund_name(cik: int | str) -> str | None:
    for fund in STARTER_FUNDS:
        if str(fund.cik) == str(cik).lstrip("0"):
            return fund.name
    return None


def followed_fund_ciks(configured: Iterable[str] | None) -> list[int]:
    """The CIKs to follow: the saved setting when it has any, else the starter list."""
    cleaned: list[int] = []
    for raw in configured or []:
        text = str(raw).strip().lstrip("0")
        if text.isdigit() and int(text) not in cleaned:
            cleaned.append(int(text))
    return cleaned or starter_fund_ciks()


# --------------------------------------------------------------------------
# 1. Parsing
# --------------------------------------------------------------------------


def _local(tag: str) -> str:
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


def _number(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _parse_us_date(raw: str | None) -> date | None:
    """13F dates are MM-DD-YYYY."""
    if not raw:
        return None
    for fmt in ("%m-%d-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def _safe_root(xml: bytes | str, what: str) -> ET.Element:
    raw = xml.encode("utf-8") if isinstance(xml, str) else xml
    if _FORBIDDEN_XML_RE.search(raw):
        raise DataProviderError(f"{what} declares a DOCTYPE or entity; refusing to parse it")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise DataProviderError(f"{what} is not valid XML: {exc}") from exc


@dataclass
class Cover13F:
    submission_type: str | None
    period: date | None
    manager_name: str | None
    is_amendment: bool
    amendment_type: str | None  # AMENDMENT_RESTATEMENT | AMENDMENT_NEW_HOLDINGS | None
    report_type: str | None  # "13F HOLDINGS REPORT" | "13F NOTICE" | "13F COMBINATION REPORT"
    table_entry_total: int | None
    table_value_total: float | None


def parse_13f_cover(xml: bytes | str) -> Cover13F:
    """The filing's primary document: period, manager, and what kind of report it is."""
    root = _safe_root(xml, "13F cover document")
    if _local(root.tag) != "edgarSubmission":
        raise DataProviderError(f"not a 13F submission (root element {_local(root.tag)!r})")
    header = _child(root, "headerData")
    filer_info = _child(header, "filerInfo")
    form_data = _child(root, "formData")
    cover = _child(form_data, "coverPage")
    summary = _child(form_data, "summaryPage")
    manager = _child(cover, "filingManager")
    amendment_info = _child(cover, "amendmentInfo")
    period = _parse_us_date(_text(_child(cover, "reportCalendarOrQuarter"))) or _parse_us_date(
        _text(_child(filer_info, "periodOfReport"))
    )
    amendment_type = (_text(_child(amendment_info, "amendmentType")) or "").upper() or None
    is_amendment = (_text(_child(cover, "isAmendment")) or "").lower() in ("true", "1", "y", "yes")
    entries = _number(_text(_child(summary, "tableEntryTotal")))
    return Cover13F(
        submission_type=_text(_child(header, "submissionType")),
        period=period,
        manager_name=_text(_child(manager, "name")),
        is_amendment=is_amendment,
        amendment_type=amendment_type,
        report_type=_text(_child(cover, "reportType")),
        table_entry_total=int(entries) if entries is not None else None,
        table_value_total=_number(_text(_child(summary, "tableValueTotal"))),
    )


@dataclass
class HoldingRow:
    """One `infoTable` row exactly as filed (value in the filing's own unit)."""

    issuer: str
    title_of_class: str | None
    cusip: str
    figi: str | None
    value_raw: float
    shares: float
    share_type: str  # "SH" shares | "PRN" principal amount
    put_call: str | None  # "Put" | "Call" | None
    discretion: str | None
    other_managers: tuple[str, ...] = ()


def parse_information_table(xml: bytes | str) -> list[HoldingRow]:
    """Every row of an information table. Namespaces vary between filers (some
    prefix every tag), so tags are matched by local name. A row without a CUSIP,
    a value or a share count cannot be identified and is skipped."""
    root = _safe_root(xml, "13F information table")
    if _local(root.tag) != "informationTable":
        raise DataProviderError(f"not an information table (root element {_local(root.tag)!r})")
    rows: list[HoldingRow] = []
    for node in _children(root, "infoTable"):
        amount = _child(node, "shrsOrPrnAmt")
        cusip = (_text(_child(node, "cusip")) or "").upper()
        value = _number(_text(_child(node, "value")))
        shares = _number(_text(_child(amount, "sshPrnamt")))
        if not cusip or value is None or shares is None:
            logger.warning("skipping a 13F row without cusip, value or share count (issuer %r)", _text(_child(node, "nameOfIssuer")))
            continue
        put_call = (_text(_child(node, "putCall")) or "").strip().capitalize() or None
        other = _text(_child(node, "otherManager"))
        rows.append(
            HoldingRow(
                issuer=_text(_child(node, "nameOfIssuer")) or "",
                title_of_class=_text(_child(node, "titleOfClass")),
                cusip=cusip,
                figi=_text(_child(node, "figi")),
                value_raw=value,
                shares=shares,
                share_type=(_text(_child(amount, "sshPrnamtType")) or "SH").upper(),
                put_call=put_call,
                discretion=_text(_child(node, "investmentDiscretion")),
                other_managers=(other,) if other else (),
            )
        )
    return rows


@dataclass
class Holding:
    """One security a manager held: the filed rows for it added together."""

    issuer: str
    title_of_class: str | None
    cusip: str
    figi: str | None
    value_raw: float
    shares: float
    share_type: str
    put_call: str | None
    row_count: int = 1
    discretion: str | None = None
    other_managers: tuple[str, ...] = ()
    symbol: str | None = None


def combine_holdings(rows: Iterable[HoldingRow]) -> list[Holding]:
    """One entry per (CUSIP, put/call, shares-or-principal).

    A manager files several rows for one security: one per sub-manager or
    discretion type (Berkshire files a row for each of its insurance companies).
    Added together they are the manager's position. Puts and calls are separate
    positions from the stock, and a bond's principal amount is never added to
    share counts, so those stay apart."""
    merged: dict[tuple[str, str | None, str], Holding] = {}
    for row in rows:
        key = (row.cusip, row.put_call, row.share_type)
        found = merged.get(key)
        if found is None:
            merged[key] = Holding(
                issuer=row.issuer,
                title_of_class=row.title_of_class,
                cusip=row.cusip,
                figi=row.figi,
                value_raw=row.value_raw,
                shares=row.shares,
                share_type=row.share_type,
                put_call=row.put_call,
                discretion=row.discretion,
                other_managers=tuple(row.other_managers),
            )
        else:
            found.value_raw += row.value_raw
            found.shares += row.shares
            found.row_count += 1
            found.other_managers = tuple(sorted(set(found.other_managers) | set(row.other_managers)))
            if found.discretion != row.discretion:
                found.discretion = "MIXED"
    return list(merged.values())


@dataclass(frozen=True)
class ValueUnit:
    multiplier: int  # raw value x multiplier = dollars
    name: str  # "thousands" | "dollars"
    how: str  # "period" | "implied_price" | "no_usable_rows"
    median_implied_price: float | None = None


def infer_value_unit(period: date | None, holdings: Iterable[Holding]) -> ValueUnit:
    """Thousands or whole dollars for this filing's `value` column.

    Periods before 2023 are in thousands by the form's own rule. From 2023 the
    form asks for whole dollars, but a manager that still reports thousands
    exists (Duquesne's June 2026 filing does). So, for those periods, the
    median value-per-share of the plain share rows is checked: priced in
    dollars it is a normal stock price; priced in thousands it is a thousandth
    of one, always under a dollar. Options rows and bond principal are left out
    of that median."""
    if period is not None and period < WHOLE_DOLLAR_FROM_PERIOD:
        return ValueUnit(1000, "thousands", "period")
    prices = [
        h.value_raw / h.shares
        for h in holdings
        if h.share_type == "SH" and h.put_call is None and h.shares > 0 and h.value_raw > 0
    ]
    if not prices:
        return ValueUnit(1, "dollars", "no_usable_rows")
    median = statistics.median(prices)
    if median < MIN_PLAUSIBLE_MEDIAN_PRICE:
        return ValueUnit(1000, "thousands", "implied_price", median)
    return ValueUnit(1, "dollars", "implied_price" if period is not None else "no_usable_rows", median)


# --------------------------------------------------------------------------
# CUSIP -> ticker, by exact name
# --------------------------------------------------------------------------

# Share classes whose issuer name is identical across classes, so the name cannot
# tell them apart. Each CUSIP below was checked against the issuer's own filings.
KNOWN_CUSIP_TICKERS: dict[str, str] = {
    "02079K305": "GOOGL",  # Alphabet Class A
    "02079K107": "GOOG",  # Alphabet Class C
    "084670702": "BRK-B",  # Berkshire Hathaway Class B
    "084670108": "BRK-A",  # Berkshire Hathaway Class A
    "35137L105": "FOXA",  # Fox Corporation Class A
    "35137L204": "FOX",  # Fox Corporation Class B
    "65249B109": "NWSA",  # News Corp Class A
    "65249B208": "NWS",  # News Corp Class B
}

# Words dropped from the END of a name, so "Apple Inc." and "APPLE INC" and
# "Apple" are the same company name. Only legal-form words and the "DEL"
# (Delaware) some filers append; nothing that can distinguish two companies.
_NAME_SUFFIXES = frozenset(
    {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC", "LP", "LLC", "NV", "SA", "AG", "DEL"}
)
# Abbreviations 13F filers use, spelled out so both sides compare equal.
_NAME_ABBREVIATIONS = {
    "HLDGS": "HOLDINGS",
    "HLDG": "HOLDING",
    "INTL": "INTERNATIONAL",
    "FINL": "FINANCIAL",
    "GRP": "GROUP",
    "TECH": "TECHNOLOGIES",
    "SYS": "SYSTEMS",
    "PPTYS": "PROPERTIES",
    "AMER": "AMERICAN",
    "NATL": "NATIONAL",
    "MGMT": "MANAGEMENT",
}
_CLASS_PAREN_RE = re.compile(r"\((?:CLASS|CL)\s+([A-Z])\)")
_CLASS_RE = re.compile(r"\b(?:CLASS|CL)\s+([A-Z])\b")


def normalise_issuer_name(name: str, title_of_class: str | None = None) -> tuple[str, str | None]:
    """(normalised name, share class letter or None).

    The class comes from "(Class B)" in a catalogue name, or "CL B" in the
    filing's name or class-title column."""
    upper = (name or "").upper()
    share_class: str | None = None
    for pattern in (_CLASS_PAREN_RE, _CLASS_RE):
        found = pattern.search(upper)
        if found:
            share_class = found.group(1)
            upper = upper[: found.start()] + " " + upper[found.end() :]
            break
    if share_class is None and title_of_class:
        found = _CLASS_RE.search(title_of_class.upper())
        if found:
            share_class = found.group(1)
    upper = upper.replace("&", " AND ")
    upper = re.sub(r"[^A-Z0-9 ]+", " ", upper)
    tokens = [_NAME_ABBREVIATIONS.get(t, t) for t in upper.split()]
    if tokens and tokens[0] == "THE":
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1] in _NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens), share_class


class IssuerMatcher:
    """Name-based CUSIP -> ticker lookup. Precision over coverage: a name that
    fits two companies, or a class that does not line up, gives None."""

    def __init__(
        self,
        catalogue: Iterable[tuple[str, str]],
        extra: Iterable[tuple[str, str]] = (),
        cusip_overrides: dict[str, str] | None = None,
    ) -> None:
        self._overrides = dict(KNOWN_CUSIP_TICKERS if cusip_overrides is None else cusip_overrides)
        self._index: dict[str, list[tuple[str, str | None]]] = {}
        first_pass = self._add(catalogue, self._index, only_missing=False)
        # The second source (SEC's company list) only fills names the first one
        # does not know: two sources disagreeing is not a reason to guess.
        self._add(extra, self._index, only_missing=True, protected=first_pass)

    @staticmethod
    def _add(
        entries: Iterable[tuple[str, str]],
        index: dict[str, list[tuple[str, str | None]]],
        *,
        only_missing: bool,
        protected: set[str] | None = None,
    ) -> set[str]:
        touched: set[str] = set()
        for symbol, name in entries:
            base, share_class = normalise_issuer_name(name)
            if not base or not symbol:
                continue
            if only_missing and protected and base in protected:
                continue
            symbol = symbol.strip().upper()
            bucket = index.setdefault(base, [])
            if (symbol, share_class) not in bucket:
                bucket.append((symbol, share_class))
            touched.add(base)
        return touched

    def match(self, cusip: str, issuer: str, title_of_class: str | None = None) -> str | None:
        override = self._overrides.get((cusip or "").upper())
        if override:
            return override
        base, share_class = normalise_issuer_name(issuer, title_of_class)
        candidates = self._index.get(base)
        if not candidates:
            return None
        symbols = {s for s, _ in candidates}
        if len(candidates) == 1:
            symbol, cand_class = candidates[0]
            if cand_class is None or share_class is None or cand_class == share_class:
                return symbol
            return None
        if len(symbols) == 1:
            return next(iter(symbols))
        same_class = [s for s, c in candidates if share_class is not None and c == share_class]
        return same_class[0] if len(set(same_class)) == 1 else None


def default_matcher(client: SecClient | None = None, *, use_sec_names: bool = True) -> IssuerMatcher:
    """A matcher over the app's company catalogue plus (best effort) SEC's list
    of company names. A failure to read SEC's list leaves the catalogue alone."""
    from app.data_providers import universe

    catalogue = {e.symbol: e.name for e in universe.load_bundled_universe() if not e.symbol.endswith("-USD")}
    for entry in universe.load_universe():
        if not entry.symbol.endswith("-USD"):
            catalogue.setdefault(entry.symbol, entry.name)
    extra: list[tuple[str, str]] = []
    if use_sec_names:
        try:
            payload = (client or get_sec_client()).get_json(TICKER_MAP_URL)
            if isinstance(payload, dict):
                extra = [
                    (str(e["ticker"]), str(e["title"]))
                    for e in payload.values()
                    if isinstance(e, dict) and e.get("ticker") and e.get("title")
                ]
        except DataProviderError as exc:
            logger.warning("SEC company list unavailable for CUSIP matching: %s", exc)
    return IssuerMatcher(catalogue.items(), extra)


# --------------------------------------------------------------------------
# 2. The filing list
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Filing13F:
    cik: int  # the MANAGER's CIK (the archive path is keyed by it)
    accession: str
    form: str  # "13F-HR" | "13F-HR/A" | "13F-NT" | "13F-NT/A"
    filing_date: date
    accepted_at: datetime  # naive UTC
    report_date: date | None  # the quarter end the filing is about
    primary_document: str  # the cover page, XSL prefix removed

    @property
    def is_notice(self) -> bool:
        return self.form in NOTICE_FORMS

    @property
    def directory_url(self) -> str:
        return ARCHIVE_DIR_URL.format(cik=self.cik, accession_nodash=self.accession.replace("-", ""))

    @property
    def index_json_url(self) -> str:
        return INDEX_JSON_URL.format(cik=self.cik, accession_nodash=self.accession.replace("-", ""))

    @property
    def cover_url(self) -> str:
        return f"{self.directory_url}/{self.primary_document}"

    @property
    def index_url(self) -> str:
        return f"{self.directory_url}/{self.accession}-index.htm"


def _iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def filings_13f_from_columns(cik: int, columns: dict) -> list[Filing13F]:
    forms = columns.get("form") or []
    accessions = columns.get("accessionNumber") or []
    filed = columns.get("filingDate") or []
    accepted = columns.get("acceptanceDateTime") or []
    documents = columns.get("primaryDocument") or []
    reports = columns.get("reportDate") or []
    out: list[Filing13F] = []
    for i, form in enumerate(forms):
        if form not in ALL_13F_FORMS:
            continue
        try:
            filing_date = _iso_date(filed[i])
            accepted_at = parse_acceptance_time(accepted[i])
            accession = accessions[i]
            document = _XSL_PREFIX_RE.sub("", documents[i]) or "primary_doc.xml"
        except (IndexError, ValueError, TypeError):
            logger.warning("skipping a malformed submissions row (form %s, index %d)", form, i)
            continue
        if filing_date is None or not accession:
            continue
        out.append(
            Filing13F(
                cik=cik,
                accession=accession,
                form=form,
                filing_date=filing_date,
                accepted_at=accepted_at,
                report_date=_iso_date(reports[i]) if i < len(reports) else None,
                primary_document=document,
            )
        )
    return out


def list_13f_filings(
    cik: int,
    since: date | None = None,
    until: date | None = None,
    *,
    client: SecClient | None = None,
    include_notices: bool = True,
) -> tuple[list[Filing13F], str | None]:
    """13F filings of one manager filed within [since, until] (by filing date),
    newest first, and the manager's registered name from the same index.

    Notices (13F-NT) say the manager's holdings are reported inside another
    manager's filing; they carry no table and are returned only so the page can
    say so."""
    client = client or get_sec_client()
    index = client.get_json(SUBMISSIONS_URL.format(cik=cik))
    if not isinstance(index, dict):
        raise DataProviderError(f"unexpected submissions payload for CIK {cik}")
    block = index.get("filings") or {}
    found = filings_13f_from_columns(cik, block.get("recent") or {})
    for page in block.get("files") or []:
        page_from, page_to = _iso_date(page.get("filingFrom")), _iso_date(page.get("filingTo"))
        if since and page_to and page_to < since:
            continue
        if until and page_from and page_from > until:
            continue
        name = page.get("name")
        if not name:
            continue
        columns = client.get_json(SUBMISSIONS_PAGE_URL.format(name=name))
        if isinstance(columns, dict):
            found.extend(filings_13f_from_columns(cik, columns))
    selected = {
        f.accession: f
        for f in found
        if (since is None or f.filing_date >= since)
        and (until is None or f.filing_date <= until)
        and (include_notices or not f.is_notice)
    }
    ordered = sorted(selected.values(), key=lambda f: (f.accepted_at, f.accession), reverse=True)
    name = index.get("name")
    return ordered, str(name) if name else None


# --------------------------------------------------------------------------
# 3. Ingest
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class IngestResult13F:
    accession: str
    form: str
    period: date | None
    holdings: int  # securities after combining rows
    created: int  # new holding facts
    already_known: int
    matched: int  # holdings that got a ticker
    unit: str | None
    new_facts: list[KnownFact] = field(default_factory=list)  # the holding facts created by this call
    filing_fact: KnownFact | None = None
    is_new_filing: bool = False


def _information_table_url(filing: Filing13F, client: SecClient) -> str:
    """The filing's information-table document. Its file name is chosen by the
    filer's software (infotable.xml, form13f_20260630.xml, 56757.xml ...), so it
    is found in the filing's own directory listing: the XML that is not the
    cover page."""
    listing = json.loads(client.get_archive_doc(filing.index_json_url).decode("utf-8"))
    items = (listing.get("directory") or {}).get("item") or []
    names = [str(i.get("name")) for i in items if str(i.get("name", "")).lower().endswith(".xml")]
    candidates = [n for n in names if n.lower() != "primary_doc.xml"]
    if not candidates:
        raise DataProviderError(f"13F filing {filing.accession} lists no information table document")
    # Several XML files are rare; prefer the one named like a table.
    candidates.sort(key=lambda n: (("info" not in n.lower() and "13f" not in n.lower()), n))
    return f"{filing.directory_url}/{candidates[0]}"


def _holding_payload(
    filing: Filing13F, cover: Cover13F, period: date | None, manager: str | None, h: Holding, unit: ValueUnit
) -> dict:
    return {
        "cik": str(filing.cik),
        "manager": manager,
        "accession": filing.accession,
        "form": filing.form,
        "period": period.isoformat() if period else None,
        "filing_date": filing.filing_date.isoformat(),
        "is_amendment": cover.is_amendment or filing.form.endswith("/A"),
        "amendment_type": cover.amendment_type,
        "cusip": h.cusip,
        "figi": h.figi,
        "issuer": h.issuer,
        "title_of_class": h.title_of_class,
        "symbol": h.symbol,
        "put_call": h.put_call,
        "share_type": h.share_type,
        "shares": h.shares,
        "value": h.value_raw * unit.multiplier,
        "value_raw": h.value_raw,
        "value_unit": unit.name,
        "row_count": h.row_count,
        "discretion": h.discretion,
        "other_managers": list(h.other_managers),
        "filing_url": filing.index_url,
    }


def ingest_13f_filing(
    session: Session,
    filing: Filing13F,
    *,
    client: SecClient | None = None,
    matcher: IssuerMatcher | None = None,
    cover_xml: bytes | str | None = None,
    table_xml: bytes | str | None = None,
    manager_name: str | None = None,
) -> IngestResult13F:
    """Fetch (or take, via `cover_xml` / `table_xml`) one filing and store it.

      holding facts: one per security, `known_at` = SEC acceptance time (basis
                     "source"), `effective_at` = the period end, keyed by
                     accession + CUSIP + put/call + shares-or-principal;
      filing fact:   one per filing (manager, period, form, totals), written
                     LAST, so a filing that has one is complete.

    A 13F-HR/A is stored under its own accession and later `known_at`; the
    readers decide, from the amendment type, whether it replaces or adds to the
    original from the moment it was accepted. A notice (13F-NT) holds no table:
    only its filing fact is written."""
    client = client or get_sec_client()
    if cover_xml is None:
        cover_xml = client.get_archive_doc(filing.cover_url)
    cover = parse_13f_cover(cover_xml)
    period = cover.period or filing.report_date
    manager = manager_name or cover.manager_name
    effective = datetime.combine(period, datetime.min.time()) if period else None

    holdings: list[Holding] = []
    unit: ValueUnit | None = None
    if not filing.is_notice:
        if table_xml is None:
            table_xml = client.get_archive_doc(_information_table_url(filing, client))
        holdings = combine_holdings(parse_information_table(table_xml))
        unit = infer_value_unit(period, holdings)
        matcher = matcher or default_matcher(client)
        for h in holdings:
            # Bond principal and options rows are not shares of the stock: no ticker.
            if h.share_type == "SH":
                h.symbol = matcher.match(h.cusip, h.issuer, h.title_of_class)

    created = known = 0
    new_facts: list[KnownFact] = []
    for h in holdings:
        assert unit is not None
        result = record_fact(
            session,
            kind=FactKind.FUND_HOLDING,
            symbol=h.symbol,
            source=SOURCE_NAME,
            source_ref=filing.index_url,
            dedupe_key=make_dedupe_key(HOLDING_PREFIX, filing.accession, h.cusip, h.put_call, h.share_type),
            known_at=filing.accepted_at,
            known_at_basis="source",
            effective_at=effective,
            payload=_holding_payload(filing, cover, period, manager, h, unit),
            commit=False,
        )
        if result.created:
            created += 1
            new_facts.append(result.fact)
        else:
            known += 1

    total_value = sum(h.value_raw * (unit.multiplier if unit else 1) for h in holdings)
    filing_result = record_fact(
        session,
        kind=FactKind.FUND_FILING,
        symbol=None,
        source=SOURCE_NAME,
        source_ref=filing.index_url,
        dedupe_key=make_dedupe_key(FILING_PREFIX, filing.accession),
        known_at=filing.accepted_at,
        known_at_basis="source",
        effective_at=effective,
        payload={
            "cik": str(filing.cik),
            "manager": manager,
            "accession": filing.accession,
            "form": filing.form,
            "period": period.isoformat() if period else None,
            "filing_date": filing.filing_date.isoformat(),
            "is_amendment": cover.is_amendment or filing.form.endswith("/A"),
            "amendment_type": cover.amendment_type,
            "is_notice": filing.is_notice,
            "report_type": cover.report_type,
            "holdings_count": len(holdings),
            "matched_count": sum(1 for h in holdings if h.symbol),
            "total_value": total_value,
            "value_unit": unit.name if unit else None,
            "value_unit_how": unit.how if unit else None,
            "median_implied_price": unit.median_implied_price if unit else None,
            "table_entry_total": cover.table_entry_total,
            "filing_url": filing.index_url,
        },
        commit=False,
    )
    session.commit()
    return IngestResult13F(
        accession=filing.accession,
        form=filing.form,
        period=period,
        holdings=len(holdings),
        created=created,
        already_known=known,
        matched=sum(1 for h in holdings if h.symbol),
        unit=unit.name if unit else None,
        new_facts=new_facts,
        filing_fact=filing_result.fact,
        is_new_filing=filing_result.created,
    )


def ingested_13f_accessions(session: Session) -> set[str]:
    """Accession numbers that have a stored filing fact (so are complete).
    Writer-side bookkeeping from the dedupe keys only, like the Form 4 one: it
    never reads what a fact says, so it cannot leak the future into a decision."""
    keys = session.exec(select(KnownFact.dedupe_key).where(KnownFact.kind == FactKind.FUND_FILING)).all()
    prefix = FILING_PREFIX + "|"
    return {k[len(prefix) :] for k in keys if k.startswith(prefix)}


# --------------------------------------------------------------------------
# 4. Backfill and incremental fetch
# --------------------------------------------------------------------------


@dataclass
class Backfill13FReport:
    funds: int = 0
    filings_seen: int = 0
    filings_ingested: int = 0
    filings_skipped_existing: int = 0
    holdings_created: int = 0
    errors: list[str] = field(default_factory=list)


def _latest_periods(filings: list[Filing13F], quarters: int) -> list[Filing13F]:
    periods = sorted({f.report_date for f in filings if f.report_date}, reverse=True)[: max(quarters, 1)]
    keep = set(periods)
    return [f for f in filings if f.report_date in keep]


def backfill_13f(
    session: Session,
    ciks: Iterable[int | str],
    quarters: int = DEFAULT_BACKFILL_QUARTERS,
    *,
    client: SecClient | None = None,
    matcher: IssuerMatcher | None = None,
    progress: Callable[[str], None] | None = None,
) -> Backfill13FReport:
    """Load each manager's latest `quarters` quarters (amendments included).

    Resumable and safe to re-run: a filing already stored is skipped without a
    request; a manager or filing that fails goes in the report and the run
    carries on. Oldest first, so an interrupted run leaves a contiguous history."""
    client = client or get_sec_client()
    report = Backfill13FReport()
    shared_matcher = matcher
    for raw in ciks:
        text = str(raw).strip().lstrip("0")
        if not text.isdigit():
            continue  # not a CIK number
        cik = int(text)
        report.funds += 1
        try:
            filings, name = list_13f_filings(cik, client=client)
        except DataProviderError as exc:
            report.errors.append(f"CIK {cik}: {exc}")
            continue
        chosen = _latest_periods(filings, quarters)
        report.filings_seen += len(chosen)
        have = ingested_13f_accessions(session)
        for filing in sorted(chosen, key=lambda f: (f.accepted_at, f.accession)):
            if filing.accession in have:
                report.filings_skipped_existing += 1
                continue
            try:
                if shared_matcher is None and not filing.is_notice:
                    shared_matcher = default_matcher(client)
                result = ingest_13f_filing(session, filing, client=client, matcher=shared_matcher, manager_name=name)
                report.filings_ingested += 1
                report.holdings_created += result.created
                if progress:
                    progress(f"{name or cik} {filing.report_date} {filing.form}: {result.holdings} holdings, {result.matched} with a ticker")
            except (DataProviderError, ValueError, json.JSONDecodeError) as exc:
                session.rollback()
                report.errors.append(f"CIK {cik} {filing.accession}: {exc}")
    return report


@dataclass
class NewFilings13F:
    results: list[IngestResult13F]
    manager_name: str | None
    filings_checked: int


def fetch_new_13f(
    session: Session,
    cik: int,
    *,
    client: SecClient | None = None,
    matcher: IssuerMatcher | None = None,
    today: date | None = None,
    lookback_days: int = NEW_FILINGS_LOOKBACK_DAYS,
) -> NewFilings13F:
    """What a watcher calls: ingest the manager's 13F filings from the last
    `lookback_days` that are not stored yet. One request for the list plus two
    or three per unseen filing."""
    client = client or get_sec_client()
    start = (today or utcnow_naive().date()) - timedelta(days=lookback_days)
    filings, name = list_13f_filings(cik, since=start, client=client)
    have = ingested_13f_accessions(session)
    results: list[IngestResult13F] = []
    fresh = [f for f in filings if f.accession not in have][:NEW_FILINGS_MAX]
    for filing in sorted(fresh, key=lambda f: (f.accepted_at, f.accession)):
        try:
            if matcher is None and not filing.is_notice:
                matcher = default_matcher(client)
            results.append(ingest_13f_filing(session, filing, client=client, matcher=matcher, manager_name=name))
        except (DataProviderError, ValueError, json.JSONDecodeError) as exc:
            session.rollback()
            if "HTTP 403" in str(exc) or "HTTP 429" in str(exc):
                raise
            logger.warning("13F filing %s of CIK %s unreadable: %s", filing.accession, cik, exc)
    return NewFilings13F(results=results, manager_name=name, filings_checked=len(fresh))
