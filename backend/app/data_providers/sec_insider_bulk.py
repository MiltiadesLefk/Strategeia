"""Bulk backfill of insider purchases and sales from SEC's quarterly "Insider Transactions Data Sets".

A new install, or a symbol newly added to the universe, has no Form 4 history: the tracker only learns
about filings from the day it starts polling. SEC publishes each finished quarter as one zip
(`{quarter}_form345.zip`, tab-separated files SUBMISSION / REPORTINGOWNER / NONDERIV_TRANS), so one
download covers every company for a quarter instead of one request per filing.

What it stores, and what it does not:
  * Open-market buys (code P) and sells (code S) of non-derivative securities, from Form 4 and 4/A,
    one `insider_trade` fact per row, in the same payload shape as `sec_form4.ingest_form4_filing`.
  * `known_at` is the END of the filing date in New York time (`known_at_basis="derived"`). The data set
    carries the filing DATE only, not the acceptance time, and a date proves only "some time that day"
    (point_in_time rule 5). That is later than the real acceptance time, never earlier, so a backtest
    cannot see a filing before it was public.
  * An accession that already has rows for the symbol (from the per-filing ingest) is skipped, so a
    filing is never stored twice under two row numbering schemes.
  * Only the symbols asked for are stored unless `symbols is None` (the market-wide scope). A row whose
    issuer symbol is missing or not a plain ticker is counted and skipped, never guessed.
  * The newest quarter is published only after it ends; the live watcher covers the days since.

One downloader at a time, over the shared rate-limited SEC client.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlmodel import Session

from app.data_providers.base import DataProviderError
from app.data_providers.sec_client import SecClient, get_sec_client
from app.data_providers.sec_form4 import DEDUPE_PREFIX, SOURCE_NAME, ingested_accessions
from app.knowledge import FactKind, make_dedupe_key, record_fact
from app.knowledge.point_in_time import end_of_local_day_utc

logger = logging.getLogger(__name__)

csv.field_size_limit(10**8)

# SEC moved newer quarters to a different folder; the older one is tried second.
URL_BASES = ("structureddata", "datastandardsinnovation")
URL_TEMPLATE = "https://www.sec.gov/files/{base}/data/insider-transactions-data-sets/{quarter}_form345.zip"
STORED_CODES = frozenset({"P", "S"})
FORM_NAMES = {"4": "4", "4/A": "4/A"}
_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}([.\-][A-Z])?$")


@dataclass
class BulkReport:
    quarter: str = ""
    filings_seen: int = 0
    filings_stored: int = 0
    rows_created: int = 0
    skipped_existing: int = 0
    skipped_symbol: int = 0  # no usable ticker, or not a symbol asked for
    errors: list[str] = field(default_factory=list)


def quarter_label(day: date) -> str:
    return f"{day.year}q{(day.month - 1) // 3 + 1}"


def quarters_between(since: date, until: date) -> list[str]:
    """Every quarter label from `since` to `until`, oldest first."""
    out: list[str] = []
    year, q = since.year, (since.month - 1) // 3 + 1
    while (year, q) <= (until.year, (until.month - 1) // 3 + 1):
        out.append(f"{year}q{q}")
        year, q = (year + 1, 1) if q == 4 else (year, q + 1)
    return out


def clean_symbol(raw: str | None) -> str | None:
    token = re.split(r"[,\s/;]+", (raw or "").upper().strip())[0]
    return token if _SYMBOL_RE.match(token) else None


def download_quarter(quarter: str, *, client: SecClient | None = None) -> bytes:
    """The zip of one quarter. Raises DataProviderError if SEC has none (a quarter still running)."""
    client = client or get_sec_client()
    last: Exception | None = None
    for base in URL_BASES:
        try:
            return client.get_bytes(URL_TEMPLATE.format(base=base, quarter=quarter))
        except DataProviderError as exc:
            last = exc
    raise DataProviderError(f"SEC has no insider data set for {quarter}: {last}")


def _rows(archive: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    with archive.open(name) as handle:
        yield from csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8", errors="replace", newline=""), delimiter="\t")


def _filing_date(raw: str) -> date | None:
    try:
        return datetime.strptime((raw or "").strip().title(), "%d-%b-%Y").date()
    except ValueError:
        return None


def _number(raw: str | None) -> float | None:
    try:
        value = float((raw or "").strip())
    except ValueError:
        return None
    return value


def _owner(row: dict[str, str]) -> dict:
    relationship = (row.get("RPTOWNER_RELATIONSHIP") or "").lower()
    cik = (row.get("RPTOWNERCIK") or "").strip() or None
    return {
        "name": (row.get("RPTOWNERNAME") or "").strip(),
        "cik": cik,
        "is_officer": "officer" in relationship,
        "is_director": "director" in relationship,
        "is_ten_percent_owner": "tenpercent" in relationship.replace(" ", "").replace("-", ""),
        "is_other": "other" in relationship,
        "officer_title": (row.get("RPTOWNER_TITLE") or "").strip() or None,
    }


def ingest_quarter_zip(
    session: Session,
    zip_bytes: bytes,
    *,
    quarter: str,
    symbols: Iterable[str] | None,
    since: date | None = None,
) -> BulkReport:
    """Store the buys and sells of one quarter's data set. `symbols=None` means every company."""
    report = BulkReport(quarter=quarter)
    wanted = None if symbols is None else {s.strip().upper() for s in symbols}
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        report.errors.append(f"{quarter}: not a zip file ({exc})")
        return report

    filings: dict[str, dict] = {}
    for row in _rows(archive, "SUBMISSION.tsv"):
        form = FORM_NAMES.get((row.get("DOCUMENT_TYPE") or "").strip().upper())
        filed = _filing_date(row.get("FILING_DATE", ""))
        if form is None or filed is None or (since is not None and filed < since):
            continue
        report.filings_seen += 1
        symbol = clean_symbol(row.get("ISSUERTRADINGSYMBOL"))
        if symbol is None or (wanted is not None and symbol not in wanted):
            report.skipped_symbol += 1
            continue
        filings[row["ACCESSION_NUMBER"]] = {
            "form": form,
            "filed": filed,
            "symbol": symbol,
            "period": _filing_date(row.get("PERIOD_OF_REPORT", "")),
            "original": _filing_date(row.get("DATE_OF_ORIG_SUB", "")),
            "issuer_cik": (row.get("ISSUERCIK") or "").strip() or None,
            "issuer_name": (row.get("ISSUERNAME") or "").strip() or None,
            "owners": [],
            "rows": [],
        }

    for row in _rows(archive, "REPORTINGOWNER.tsv"):
        filing = filings.get(row["ACCESSION_NUMBER"])
        if filing is not None:
            filing["owners"].append(_owner(row))
    for row in _rows(archive, "NONDERIV_TRANS.tsv"):
        filing = filings.get(row["ACCESSION_NUMBER"])
        if filing is not None and (row.get("TRANS_CODE") or "").strip().upper() in STORED_CODES:
            filing["rows"].append(row)

    stored_by_symbol: dict[str, set[str]] = {}
    for accession, filing in filings.items():
        if not filing["rows"]:
            continue
        symbol = filing["symbol"]
        if symbol not in stored_by_symbol:
            stored_by_symbol[symbol] = ingested_accessions(session, symbol)
        if accession in stored_by_symbol[symbol]:
            report.skipped_existing += 1
            continue
        created = _store_filing(session, accession, filing)
        report.rows_created += created
        report.filings_stored += 1 if created else 0
    return report


def _store_filing(session: Session, accession: str, filing: dict) -> int:
    owners = filing["owners"]
    primary = owners[0] if owners else None
    known_at = end_of_local_day_utc(filing["filed"])
    created = 0
    for index, row in enumerate(sorted(filing["rows"], key=lambda r: int(r.get("NONDERIV_TRANS_SK") or 0))):
        shares = _number(row.get("TRANS_SHARES"))
        price = _number(row.get("TRANS_PRICEPERSHARE"))
        if price is not None and price <= 0:
            price = None  # a footnote-only price, never zero
        traded = _filing_date(row.get("TRANS_DATE", ""))
        payload = {
            "accession": accession,
            "row_index": index,
            "form": filing["form"],
            "filing_date": filing["filed"].isoformat(),
            "period_of_report": filing["period"].isoformat() if filing["period"] else None,
            "is_amendment": filing["form"] == "4/A",
            "date_of_original_submission": filing["original"].isoformat() if filing["original"] else None,
            "issuer_cik": filing["issuer_cik"],
            "issuer_name": filing["issuer_name"],
            "issuer_symbol": filing["symbol"],
            "symbol": filing["symbol"],
            "table": "non_derivative",
            "security_title": (row.get("SECURITY_TITLE") or "").strip() or None,
            "transaction_date": traded.isoformat() if traded else None,
            "code": (row.get("TRANS_CODE") or "").strip().upper(),
            "acquired_disposed": (row.get("TRANS_ACQUIRED_DISP_CD") or "").strip().upper() or None,
            "shares": shares,
            "price": price,
            "value": shares * price if shares is not None and price is not None else None,
            "shares_after": _number(row.get("SHRS_OWNED_FOLLOWING_TRANS")),
            "ownership": (row.get("DIRECT_INDIRECT_OWNERSHIP") or "").strip() or None,
            "is_10b5_1": False,  # the data set has no per-row plan flag; absent is "no flag", not "no plan"
            "plan_flag_source": None,
            "equity_swap": (row.get("EQUITY_SWAP_INVOLVED") or "").strip() in ("1", "true", "True"),
            "owner_name": primary["name"] if primary else None,
            "owner_cik": primary["cik"] if primary else None,
            "owner_key": (primary["cik"] or primary["name"].strip().upper()) if primary else None,
            "is_officer": primary["is_officer"] if primary else False,
            "is_director": primary["is_director"] if primary else False,
            "is_ten_percent_owner": primary["is_ten_percent_owner"] if primary else False,
            "is_other": primary["is_other"] if primary else False,
            "officer_title": primary["officer_title"] if primary else None,
            "owners": owners,
            "filing_url": None,
            "from_bulk_dataset": True,
        }
        result = record_fact(
            session,
            kind=FactKind.INSIDER_TRADE,
            symbol=filing["symbol"],
            source=SOURCE_NAME,
            source_ref=f"sec-insider-bulk:{accession}",
            dedupe_key=make_dedupe_key(DEDUPE_PREFIX, accession, index),
            known_at=known_at,
            known_at_basis="derived",
            effective_at=datetime.combine(traded, datetime.min.time()) if traded else None,
            payload=payload,
        )
        created += 1 if result.created else 0
    return created


def backfill_from_bulk(
    session: Session,
    since: date,
    until: date,
    *,
    symbols: Iterable[str] | None,
    client: SecClient | None = None,
) -> list[BulkReport]:
    """Download and store every finished quarter between `since` and `until`. A quarter SEC has not
    published yet is reported, not an error: the live watcher covers it."""
    reports: list[BulkReport] = []
    wanted = None if symbols is None else list(symbols)
    for quarter in quarters_between(since, until):
        try:
            data = download_quarter(quarter, client=client)
        except DataProviderError as exc:
            reports.append(BulkReport(quarter=quarter, errors=[str(exc)]))
            continue
        reports.append(ingest_quarter_zip(session, data, quarter=quarter, symbols=wanted, since=since))
    return reports


__all__ = ["BulkReport", "backfill_from_bulk", "ingest_quarter_zip", "quarters_between", "clean_symbol"]
