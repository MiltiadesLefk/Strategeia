"""House trade reports: reading the PDF (real public reports saved under
tests/fixtures/house), the amount ranges, the yearly index, and storing each trade
with the FILING date as the moment it became known. No network."""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.data_providers import house_disclosures as hd
from app.data_providers.base import DataProviderError
from app.data_providers.house_disclosures import (
    HouseFiling,
    backfill_house,
    filing_known_at,
    index_years,
    ingest_ptr,
    ingested_doc_ids,
    list_ptr_filings,
    parse_amount,
    parse_index_xml,
    parse_index_zip,
    parse_ptr_pdf,
    range_midpoint,
    split_asset,
)
from app.knowledge import FactKind, KnownFact
from app.knowledge.congress_trades import CONGRESS_FILING_KIND

FIXTURES = Path(__file__).parent / "fixtures" / "house"
PELOSI, KEAN, ROSE, GREENE, SCANNED, DOGGETT = "20026590", "20026802", "20030610", "20026658", "8220757", "20030618"


def pdf_bytes(doc_id: str) -> bytes:
    return (FIXTURES / f"{doc_id}.pdf").read_bytes()


def index_zip_bytes(year: int = 2025) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{year}FD.xml", (FIXTURES / "2025FD_excerpt.xml").read_bytes())
    return buf.getvalue()


class FakeHouse:
    """Stands in for the HTTP client: the index zip and the saved PDFs, with a
    request counter and a switch to make a document fail."""

    def __init__(self, missing: set[str] | None = None, index_ok: bool = True):
        self.requests: list[str] = []
        self.missing = missing or set()
        self.index_ok = index_ok

    def _key(self, url: str) -> str:
        return url.rsplit("/", 1)[-1]

    def get_bytes(self, url: str) -> bytes:
        self.requests.append(url)
        if url.endswith("FD.zip"):
            if not self.index_ok:
                raise DataProviderError("house index unreachable")
            return index_zip_bytes()
        return self.get_archive_doc(url)

    def get_archive_doc(self, url: str) -> bytes:
        self.requests.append(url)
        doc_id = self._key(url).removesuffix(".pdf")
        if doc_id in self.missing or not (FIXTURES / f"{doc_id}.pdf").exists():
            raise DataProviderError(f"fetch failed for {url}: HTTP 404")
        return pdf_bytes(doc_id)

    def pdf_requests(self) -> list[str]:
        return [u for u in self.requests if u.endswith(".pdf")]


@pytest.fixture(autouse=True)
def _clear_cache():
    hd.clear_index_cache()


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def filing_of(doc_id: str) -> HouseFiling:
    return next(f for f in hd.parse_index_xml((FIXTURES / "2025FD_excerpt.xml").read_bytes(), 2025) if f.doc_id == doc_id)


# --------------------------------------------------------------------------
# amounts and asset names
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("$1,001 - $15,000", (1001, 15000)),
        ("$250,001 -\n$500,000", (250001, 500000)),
        ("$15,001-$50,000", (15001, 50000)),
        ("$5,000,001 - $25,000,000", (5_000_001, 25_000_000)),
        ("Over $50,000,000", (50_000_000, None)),
        ("$1,000,001 +", (1_000_001, None)),
    ],
)
def test_parse_amount_keeps_the_range(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["", "about $5,000", "$20,000 - $10,000", "1,001 - 15,000"])
def test_parse_amount_refuses_what_is_not_a_range(text):
    assert parse_amount(text) is None


def test_range_midpoint_is_none_for_an_open_range():
    assert range_midpoint(1001, 15000) == 8000.5
    assert range_midpoint(50_000_000, None) is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Apple Inc. - Common Stock (AAPL) [ST]", ("Apple Inc. - Common Stock (AAPL)", "ST", "AAPL")),
        ("Berkshire Hathaway Inc. New (BRK.B) [ST]", ("Berkshire Hathaway Inc. New (BRK.B)", "ST", "BRK.B")),
        ("AMERICAN FUNDS EUPAC FUND CL A M/F (Symbol: AEPGX) [OT]", ("AMERICAN FUNDS EUPAC FUND CL A M/F (Symbol: AEPGX)", "OT", "AEPGX")),
        ("US Treasury Bill [GS]", ("US Treasury Bill", "GS", None)),
        ("Kean Family Partnership (33% Interest) [OL]", ("Kean Family Partnership (33% Interest)", "OL", None)),
        ("Some Company (Class A)", ("Some Company (Class A)", None, None)),
    ],
)
def test_split_asset(text, expected):
    assert split_asset(text) == expected


# --------------------------------------------------------------------------
# the PDF reader, on real reports
# --------------------------------------------------------------------------


def test_a_spouse_report_is_read_row_by_row():
    parsed = parse_ptr_pdf(pdf_bytes(PELOSI))
    assert parsed.status == "ok" and parsed.skipped_blocks == 0
    assert parsed.name_in_pdf == "Hon. Nancy Pelosi" and parsed.state_district_in_pdf == "CA11"
    assert len(parsed.rows) == 9
    assert [r.row_index for r in parsed.rows] == list(range(9))
    assert {r.owner_code for r in parsed.rows} == {"SP"} and {r.owner for r in parsed.rows} == {"spouse"}

    first = parsed.rows[0]
    assert first.asset == "Alphabet Inc. - Class A Common Stock (GOOGL)"
    assert (first.asset_type, first.ticker, first.type_code, first.side) == ("OP", "GOOGL", "P", "buy")
    assert (first.trade_date, first.notification_date) == (date(2025, 1, 14), date(2025, 1, 14))
    # The amount range wraps over two lines in the PDF.
    assert (first.amount_low, first.amount_high) == (250_001, 500_000)
    assert first.amount_text == "$250,001 - $500,000"
    assert first.filing_status == "New" and "call options" in first.notes
    # An option is stored with its ticker but is not a stock.
    assert first.symbol == "GOOGL"

    apple = parsed.rows[2]
    assert (apple.asset_type, apple.type_code, apple.side) == ("ST", "S (partial)", "sell")
    assert (apple.amount_low, apple.amount_high) == (5_000_001, 25_000_000)
    assert apple.trade_date == date(2024, 12, 31)


def test_a_filer_with_no_owner_column_is_the_member_and_footers_are_not_asset_text():
    parsed = parse_ptr_pdf(pdf_bytes(KEAN))
    assert parsed.status == "ok" and len(parsed.rows) == 3
    assert [r.ticker for r in parsed.rows] == ["AMZN", "CCK", "MSFT"]
    assert {r.owner for r in parsed.rows} == {"self"}
    assert all("Kean Family" not in r.asset for r in parsed.rows)
    assert all("Kean Family Partnership" in r.notes for r in parsed.rows)  # the "subholding of" footer


def test_funds_and_bonds_have_no_symbol_to_score():
    parsed = parse_ptr_pdf(pdf_bytes(ROSE))
    assert parsed.status == "ok" and len(parsed.rows) == 10
    funds = [r for r in parsed.rows if r.asset_type == "OT"]
    assert funds and all(r.symbol is None for r in funds)
    assert funds[0].ticker == "CWGIX"  # kept for display
    stocks = [r for r in parsed.rows if r.asset_type == "ST"]
    assert {r.symbol for r in stocks} >= {"GOOGL", "GOOG", "MSFT"}

    greene = parse_ptr_pdf(pdf_bytes(GREENE))
    bills = [r for r in greene.rows if r.asset.startswith("US Treasury Bill")]
    assert bills and all(r.ticker is None and r.symbol is None for r in bills)


def test_a_row_split_over_a_page_break_is_read_once_with_its_whole_name():
    parsed = parse_ptr_pdf(pdf_bytes(GREENE))
    assert parsed.status == "ok" and len(parsed.rows) == 56
    goog = [r for r in parsed.rows if r.ticker == "GOOG"]
    assert len(goog) == 3  # one of them straddles the page break
    assert all(r.asset == "Alphabet Inc. - Class C Capital Stock (GOOG)" for r in goog)
    assert all(r.type_code == "P" and (r.amount_low, r.amount_high) == (1001, 15000) for r in parsed.rows[:5])


def test_a_scanned_report_is_unreadable_never_guessed():
    parsed = parse_ptr_pdf(pdf_bytes(SCANNED))
    assert parsed.status == "unreadable"
    assert parsed.rows == [] and "scanned" in parsed.reason


@pytest.mark.parametrize("junk", [b"", b"not a pdf at all", b"%PDF-1.4 truncated"])
def test_a_broken_file_is_unreadable_and_never_raises(junk):
    parsed = parse_ptr_pdf(junk)
    assert parsed.status == "unreadable" and parsed.rows == []


# --------------------------------------------------------------------------
# the yearly index
# --------------------------------------------------------------------------


def test_the_index_lists_every_filing_and_builds_the_member_name():
    filings = parse_index_zip(index_zip_bytes(), 2025)
    assert len(filings) == 7
    kean = next(f for f in filings if f.doc_id == KEAN)
    assert kean.filing_type == "P" and kean.filing_date == date(2025, 2, 19)
    assert kean.member_name == "Thomas H. Kean Jr" and kean.state_district == "NJ07"
    assert kean.url == f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/{KEAN}.pdf"
    assert next(f for f in filings if f.doc_id == "10072640").filing_type == "C"


def test_only_trade_reports_in_the_date_range_are_listed(monkeypatch):
    fake = FakeHouse()
    listed = list_ptr_filings(2025, client=fake)
    assert [f.doc_id for f in listed] == [PELOSI, GREENE, SCANNED, KEAN, ROSE, DOGGETT] or {f.doc_id for f in listed} == {
        PELOSI, GREENE, SCANNED, KEAN, ROSE, DOGGETT,
    }
    assert [f.filing_date for f in listed] == sorted(f.filing_date for f in listed)  # oldest first
    window = list_ptr_filings(2025, date(2025, 2, 1), date(2025, 6, 30), client=fake)
    assert {f.doc_id for f in window} == {SCANNED, KEAN}
    # The index is held in memory: three lookups, one download.
    assert sum(1 for u in fake.requests if u.endswith("FD.zip")) == 1


def test_index_without_a_document_id_or_a_date_is_skipped_honestly():
    xml = b"""<FinancialDisclosure>
      <Member><Last>A</Last><First>B</First><FilingType>P</FilingType><Year>2025</Year><FilingDate>1/2/2025</FilingDate><DocID></DocID></Member>
      <Member><Last>C</Last><First>D</First><FilingType>P</FilingType><Year>2025</Year><FilingDate></FilingDate><DocID>20000001</DocID></Member>
    </FinancialDisclosure>"""
    filings = parse_index_xml(xml, 2025)
    assert [f.doc_id for f in filings] == ["20000001"] and filings[0].filing_date is None

    class One(FakeHouse):
        def get_bytes(self, url):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("2025FD.xml", xml)
            return buf.getvalue()

    # No filing date, no honest known_at: not listed.
    assert list_ptr_filings(2025, client=One()) == []


def test_xml_with_an_entity_declaration_is_refused():
    with pytest.raises(DataProviderError):
        parse_index_xml(b'<!DOCTYPE x [<!ENTITY a "b">]><FinancialDisclosure/>', 2025)
    with pytest.raises(DataProviderError):
        parse_index_zip(b"this is not a zip", 2025)


def test_january_and_february_also_read_the_previous_years_index():
    assert index_years(date(2026, 1, 15)) == [2026, 2025]
    assert index_years(date(2026, 3, 1)) == [2026]


# --------------------------------------------------------------------------
# storing: known_at is the FILING date
# --------------------------------------------------------------------------


def test_known_at_is_the_end_of_the_filing_day_in_new_york_and_never_in_the_future():
    # 17 Jan 2025 ends at 05:00 UTC on the 18th (EST).
    assert filing_known_at(date(2025, 1, 17), now=datetime(2026, 1, 1)) == datetime(2025, 1, 18, 4, 59, 59, 999999)
    # Filed today and fetched this morning: not public later than we fetched it.
    now = datetime(2025, 1, 17, 15, 0)
    assert filing_known_at(date(2025, 1, 17), now=now) == now


def test_each_row_is_a_fact_dated_by_the_filing_not_the_trade(session):
    result = ingest_ptr(session, filing_of(PELOSI), pdf_bytes(PELOSI))
    assert (result.status, result.rows_created, result.rows_total) == ("ok", 9, 9)
    facts = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.CONGRESS_TRADE)).all()
    assert len(facts) == 9
    nvda_buy = next(f for f in facts if f.payload["ticker"] == "NVDA" and f.payload["side"] == "buy" and f.payload["asset_type"] == "ST")
    assert nvda_buy.symbol == "NVDA"
    # Traded 20 Dec 2024, filed 17 Jan 2025: public from the filing day, not the trade day.
    assert nvda_buy.effective_at == datetime(2024, 12, 20)
    assert nvda_buy.known_at == datetime(2025, 1, 18, 4, 59, 59, 999999)
    assert nvda_buy.known_at_basis == "derived"
    p = nvda_buy.payload
    assert (p["member"], p["state_district"], p["owner"], p["amount_low"], p["amount_high"]) == (
        "Nancy Pelosi", "CA11", "spouse", 500_001, 1_000_000,
    )
    assert (p["trade_date"], p["filed_date"]) == ("2024-12-20", "2025-01-17")
    assert nvda_buy.source_ref == filing_of(PELOSI).url
    assert nvda_buy.dedupe_key == f"house|{PELOSI}|{p['row_index']}"


def test_a_row_without_a_stock_ticker_is_stored_with_no_symbol(session):
    ingest_ptr(session, filing_of(ROSE), pdf_bytes(ROSE))
    facts = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.CONGRESS_TRADE)).all()
    assert len(facts) == 10
    assert all(f.symbol is None for f in facts if f.payload["asset_type"] == "OT")
    assert {f.symbol for f in facts if f.payload["asset_type"] == "ST"} == {"GOOGL", "GOOG", "MSFT"} | {
        f.symbol for f in facts if f.payload["asset_type"] == "ST"
    }


def test_ingesting_twice_adds_nothing_and_a_scanned_report_is_remembered(session):
    first = ingest_ptr(session, filing_of(KEAN), pdf_bytes(KEAN))
    again = ingest_ptr(session, filing_of(KEAN), pdf_bytes(KEAN))
    assert (first.rows_created, again.rows_created) == (3, 0)

    scanned = ingest_ptr(session, filing_of(SCANNED), pdf_bytes(SCANNED))
    assert scanned.status == "unreadable" and scanned.rows_total == 0
    record = session.exec(
        select(KnownFact).where(KnownFact.kind == CONGRESS_FILING_KIND, KnownFact.dedupe_key == f"house|{SCANNED}")
    ).one()
    assert record.payload["status"] == "unreadable" and "scanned" in record.payload["reason"]
    assert ingested_doc_ids(session) == {KEAN, SCANNED}


# --------------------------------------------------------------------------
# backfill
# --------------------------------------------------------------------------


def test_backfill_is_resumable_and_retries_only_what_failed(session):
    fake = FakeHouse(missing={ROSE})
    first = backfill_house(session, 2025, client=fake)
    # Doggett has no saved PDF, Rose is made to fail: both are errors, not facts.
    assert first.filings_listed == 6 and first.filings_ingested == 4
    assert {e.split(":")[0] for e in first.errors} == {ROSE, DOGGETT}
    assert first.unreadable == 1 and first.rows_created == 9 + 3 + 56
    assert ingested_doc_ids(session) == {PELOSI, GREENE, SCANNED, KEAN}

    fake.requests.clear()
    fake.missing.clear()
    second = backfill_house(session, 2025, client=fake)
    assert second.filings_skipped_existing == 4 and second.filings_ingested == 1  # Rose now; Doggett still absent
    assert len(second.errors) == 1 and second.rows_created == 10
    assert sorted(u.rsplit("/", 1)[-1] for u in fake.pdf_requests()) == [f"{ROSE}.pdf", f"{DOGGETT}.pdf"]


def test_backfill_can_be_bounded_and_says_how_many_are_left(session):
    fake = FakeHouse()
    report = backfill_house(session, 2025, since=date(2025, 1, 1), until=date(2025, 12, 31), max_filings=2, client=fake)
    assert report.filings_ingested == 2 and report.filings_remaining == 4  # six listed, two done now
    assert len(fake.pdf_requests()) == 2


def test_backfill_reports_progress(session):
    seen = []
    backfill_house(session, 2025, max_filings=1, client=FakeHouse(), progress=seen.append)
    assert len(seen) == 1 and seen[0].filings_done == 1 and seen[0].filings_total == 1
