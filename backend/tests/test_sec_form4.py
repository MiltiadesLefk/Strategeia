"""Form 4 parsing, the filing list, ingest and backfill. Fixtures are real filings
(tests/fixtures/form4); nothing touches the network."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.data_providers import sec_form4
from app.data_providers.base import DataProviderError
from app.data_providers.sec_client import RateLimiter, SecClient
from app.data_providers.sec_form4 import (
    Form4Filing,
    backfill_insider_trades,
    fetch_new_insider_trades,
    ingest_form4_filing,
    list_form4_filings,
    parse_acceptance_time,
    parse_form4_xml,
)
from app.knowledge import FactKind, KnownFact

FIXTURES = Path(__file__).parent / "fixtures" / "form4"
ET_ZONE = ZoneInfo("America/New_York")
UTC_ZONE = ZoneInfo("UTC")


def xml_of(name: str) -> bytes:
    return (FIXTURES / f"{name}.xml").read_bytes()


# name -> (issuer cik, accession, SEC acceptance time as the submissions index states it, document)
FILINGS = {
    "unh_000140": (731766, "0000731766-25-000140", "2025-05-16T16:22:34.000Z", "wk-form4_1747412548.xml"),
    "unh_000142": (731766, "0000731766-25-000142", "2025-05-16T16:28:22.000Z", "wk-form4_1747412895.xml"),
    "unh_000146": (731766, "0000731766-25-000146", "2025-05-16T22:11:47.000Z", "wk-form4_1747433501.xml"),
    "aapl_orig": (320193, "0000320193-22-000076", "2022-08-19T22:30:27.000Z", "wf-form4_166094821148314.xml"),
    "aapl_4a": (320193, "0000320193-22-000078", "2022-08-22T22:43:18.000Z", "wf-form4a_166120817922787.xml"),
    "aapl_013191": (320193, "0001140361-26-013191", "2026-04-03T22:30:43.000Z", "form4.xml"),
    "aapl_038028": (320193, "0001140361-26-038028", "2026-09-29T22:44:50.000Z", "form4.xml"),
}
FILED = {
    "unh_000140": "2025-05-16",
    "unh_000142": "2025-05-16",
    "unh_000146": "2025-05-16",
    "aapl_orig": "2022-08-19",
    "aapl_4a": "2022-08-22",
    "aapl_013191": "2026-04-03",
    "aapl_038028": "2026-09-29",
}


def filing_of(name: str) -> Form4Filing:
    cik, accession, accepted, document = FILINGS[name]
    return Form4Filing(
        cik=cik,
        accession=accession,
        form="4/A" if name == "aapl_4a" else "4",
        filing_date=date.fromisoformat(FILED[name]),
        accepted_at=parse_acceptance_time(accepted),
        primary_document=document,
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ---- parser: exact values, read by hand from the filings ----


def test_unh_director_purchase():
    doc = parse_form4_xml(xml_of("unh_000140"))
    assert doc.document_type == "4" and not doc.is_amendment
    assert (doc.issuer_cik, doc.issuer_symbol, doc.issuer_name) == ("0000731766", "UNH", "UNITEDHEALTH GROUP INC")
    assert doc.period_of_report == date(2025, 5, 14)
    (owner,) = doc.owners
    assert (owner.name, owner.cik) == ("Noseworthy John H", "0001706596")
    assert (owner.is_director, owner.is_officer, owner.is_ten_percent_owner, owner.is_other) == (
        True,
        False,
        False,
        False,
    )
    assert owner.officer_title is None
    (row,) = doc.transactions
    assert (row.table, row.code, row.acquired_disposed, row.transaction_date) == (
        "non_derivative",
        "P",
        "A",
        date(2025, 5, 14),
    )
    assert (row.shares, row.price, row.shares_after, row.ownership) == (300.0, 312.1563, 6063.0, "D")
    assert row.value == pytest.approx(300 * 312.1563)
    assert not row.is_10b5_1 and doc.holding_rows == 0


def test_officer_with_a_price_footnote_and_a_holding_row():
    doc = parse_form4_xml(xml_of("unh_000146"))
    owner = doc.owners[0]
    assert (owner.name, owner.is_officer, owner.is_director, owner.officer_title) == (
        "REX JOHN F",
        True,
        False,
        "President & CFO",
    )
    # The indirect "By Trust" holding has no transaction: it must not become a row.
    assert len(doc.transactions) == 1 and doc.holding_rows == 1
    row = doc.transactions[0]
    assert (row.code, row.shares, row.price, row.shares_after) == ("P", 17175.0, 291.1161, 203796.038)
    assert row.footnote_ids == ["F1"] and "weighted average purchase price" in row.footnotes[0]
    assert not row.is_10b5_1  # the footnote does not mention a plan


def test_indirect_ownership_nature():
    row = parse_form4_xml(xml_of("unh_000142")).transactions[0]
    assert (row.ownership, row.ownership_nature, row.shares, row.price) == ("I", "By Trust", 1533.0, 320.80)


def test_two_sales_under_a_10b5_1_plan_stated_in_a_footnote():
    doc = parse_form4_xml(xml_of("aapl_orig"))
    first, second = doc.transactions
    assert [r.row_index for r in doc.transactions] == [0, 1]
    assert (first.code, first.acquired_disposed, first.shares, first.price, first.shares_after) == (
        "S",
        "D",
        66390.0,
        174.66,
        141018.0,
    )
    assert (second.shares, second.price, second.shares_after) == (30345.0, 175.60, 110673.0)
    # Each row keeps its OWN price: the regex this replaced paired them by position.
    assert first.footnote_ids == ["F1", "F2"] and second.footnote_ids == ["F1", "F3"]
    assert first.is_10b5_1 and first.plan_flag_source == "footnote"
    assert not doc.aff_10b5_1  # no checkbox in a 2022 filing
    assert doc.owners[0].officer_title == "Senior Vice President, CFO"


def test_amendment_is_linked_to_the_original_by_date():
    doc = parse_form4_xml(xml_of("aapl_4a"))
    assert doc.document_type == "4/A" and doc.is_amendment
    assert doc.date_of_original_submission == date(2022, 8, 19)
    assert [(r.code, r.shares, r.price) for r in doc.transactions] == [("S", 66390.0, 174.66), ("S", 30345.0, 175.6)]
    # The amendment corrects the plan dates, and says so in its footnotes.
    assert "November 5, 2021" in doc.transactions[0].footnotes[0]
    assert "amendment corrects" in " ".join(doc.transactions[0].footnotes)


def test_missing_price_is_none_never_zero_and_derivative_rows_are_parsed():
    doc = parse_form4_xml(xml_of("aapl_013191"))
    rows = doc.transactions
    assert [(r.row_index, r.table, r.code) for r in rows] == [
        (0, "non_derivative", "M"),
        (1, "non_derivative", "F"),
        (2, "derivative", "M"),
        (3, "derivative", "M"),
        (4, "derivative", "M"),
    ]
    assert rows[0].price is None and rows[0].value is None  # only a footnote reference
    assert rows[0].shares == 64317.0 and rows[0].shares_after == 1107212.0
    assert (rows[1].price, rows[1].shares, rows[1].acquired_disposed) == (255.63, 33317.0, "D")
    assert rows[2].underlying_title == "Common Stock" and rows[2].underlying_shares == 22688.0
    assert rows[2].shares_after == 0.0  # a real zero is kept
    assert doc.holding_rows == 1
    assert doc.owners[0].is_director is False  # tags absent from the filing default to false


def test_true_false_booleans_and_a_zero_price_grant():
    doc = parse_form4_xml(xml_of("aapl_038028"))
    owner = doc.owners[0]
    assert (owner.is_officer, owner.is_director, owner.officer_title) == (True, False, "COO")
    assert not doc.aff_10b5_1
    assert [r.table for r in doc.transactions] == ["derivative", "derivative"]
    assert doc.transactions[0].price == 0.0  # an explicit 0.00 grant price is a real zero
    assert doc.transactions[0].code_meaning == "Grant, award or other acquisition from the issuer"


JOINT = """<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <periodOfReport>2025-03-03</periodOfReport>
  <issuer><issuerCik>0000111111</issuerCik><issuerName>ACME CORP</issuerName><issuerTradingSymbol>ACME</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0000222222</rptOwnerCik><rptOwnerName>Big Fund LP</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isTenPercentOwner>true</isTenPercentOwner><isOther>1</isOther><otherText>Member of a group</otherText></reportingOwnerRelationship>
  </reportingOwner>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Jane Founder</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <aff10b5One>1</aff10b5One>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common</value></securityTitle>
      <transactionDate><value>2025-03-03</value></transactionDate>
      <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>P</transactionCode><equitySwapInvolved>0</equitySwapInvolved></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1,000</value></transactionShares>
        <transactionPricePerShare><value>10.5</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>5000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>I</value></directOrIndirectOwnership></ownershipNature>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_joint_filers_checkbox_flag_and_missing_owner_cik():
    doc = parse_form4_xml(JOINT)
    big, jane = doc.owners
    assert (big.name, big.cik, big.is_ten_percent_owner, big.is_other, big.other_text) == (
        "Big Fund LP",
        "0000222222",
        True,
        True,
        "Member of a group",
    )
    assert (jane.name, jane.cik, jane.is_director, jane.is_officer, jane.officer_title) == (
        "Jane Founder",
        None,
        True,
        True,
        "CEO",
    )
    assert jane.key == "JANE FOUNDER" and big.key == "0000222222"
    assert doc.aff_10b5_1
    row = doc.transactions[0]
    assert row.shares == 1000.0 and row.is_10b5_1 and row.plan_flag_source == "checkbox"


@pytest.mark.parametrize(
    "bad",
    [b"not xml at all", b"<html></html>", b'<!DOCTYPE x [<!ENTITY a "b">]><ownershipDocument/>'],
)
def test_not_a_form4_raises_a_clean_error(bad):
    with pytest.raises(DataProviderError):
        parse_form4_xml(bad)


# ---- acceptance time ----


def test_acceptance_time_is_utc_and_matches_the_new_york_wall_clock():
    accepted = parse_acceptance_time("2022-08-19T22:30:27.000Z")
    assert accepted == datetime(2022, 8, 19, 22, 30, 27) and accepted.tzinfo is None
    local = accepted.replace(tzinfo=UTC_ZONE).astimezone(ET_ZONE)
    assert (local.hour, local.minute, local.second) == (18, 30, 27)  # EDT: after the 17:30 cutoff


def test_a_filing_after_5_30_pm_new_york_is_stamped_after_that_day_s_cutoff():
    accepted = parse_acceptance_time("2025-05-16T22:11:47.000Z")  # 18:11:47 EDT
    local = accepted.replace(tzinfo=UTC_ZONE).astimezone(ET_ZONE)
    assert local.hour == 18 and local.date() == date(2025, 5, 16)


def test_acceptance_time_across_the_dst_boundary():
    def ny(raw):
        return parse_acceptance_time(raw).replace(tzinfo=UTC_ZONE).astimezone(ET_ZONE)

    # 22:30Z is 17:30 in winter (EST, UTC-5) and 18:30 in summer (EDT, UTC-4).
    assert (ny("2025-12-01T22:30:00.000Z").hour, ny("2025-12-01T22:30:00.000Z").minute) == (17, 30)
    assert (ny("2025-07-01T22:30:00.000Z").hour, ny("2025-07-01T22:30:00.000Z").minute) == (18, 30)
    # Clocks changed on 2025-03-09: the same UTC time reads an hour later in New York afterwards.
    assert (ny("2025-03-08T22:30:00.000Z").hour, ny("2025-03-10T22:30:00.000Z").hour) == (17, 18)


# ---- the filing list ----


def columns(rows):
    keys = ["accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "primaryDocument"]
    return {k: [r[i] for r in rows] for i, k in enumerate(keys)}


RECENT = columns(
    [
        ("0000320193-26-000010", "2026-09-01", "2026-08-30", "2026-09-01T21:00:00.000Z", "4", "xslF345X06/form4.xml"),
        ("0000320193-26-000009", "2026-08-20", "", "2026-08-20T20:00:00.000Z", "8-K", "x.htm"),
        ("0000320193-22-000078", "2022-08-22", "2022-08-17", "2022-08-22T22:43:18.000Z", "4/A", "xslF345X03/wf-form4a.xml"),
    ]
)
OLD_PAGE = columns(
    [("0000320193-15-000001", "2015-03-02", "2015-02-27", "2015-03-02T21:15:00.000Z", "4", "xslF345X02/doc4.xml")]
)


class FakeSec:
    """Serves submissions JSON and archive documents from memory."""

    def __init__(self, documents: dict[str, bytes] | None = None):
        self.urls: list[str] = []
        self.documents = documents or {}
        self.fail: set[str] = set()
        self.submissions = {
            320193: {
                "filings": {
                    "recent": RECENT,
                    "files": [
                        {
                            "name": "CIK0000320193-submissions-001.json",
                            "filingFrom": "2010-01-01",
                            "filingTo": "2016-12-31",
                        }
                    ],
                }
            }
        }
        self.pages = {"CIK0000320193-submissions-001.json": OLD_PAGE}
        self.ticker_map = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}

        def http_get(url, headers, timeout):
            self.urls.append(url)
            if url in self.fail:
                return 500, {}, b""
            if url == sec_form4.TICKER_MAP_URL:
                return 200, {}, json.dumps(self.ticker_map).encode()
            for cik, payload in self.submissions.items():
                if url == sec_form4.SUBMISSIONS_URL.format(cik=cik):
                    return 200, {}, json.dumps(payload).encode()
            for name, page in self.pages.items():
                if url == sec_form4.SUBMISSIONS_PAGE_URL.format(name=name):
                    return 200, {}, json.dumps(page).encode()
            if url in self.documents:
                return 200, {}, self.documents[url]
            return 404, {}, b""

        self.client = SecClient(
            limiter=RateLimiter(rate=1000, clock=lambda: 0.0, sleep=lambda s: None),
            http_get=http_get,
            sleep=lambda s: None,
        )


@pytest.fixture(autouse=True)
def _fresh_ticker_map(monkeypatch):
    monkeypatch.setattr(sec_form4, "_ticker_map", None)


def test_list_combines_recent_and_older_pages_strips_xsl_and_skips_other_forms():
    sec = FakeSec()
    found = list_form4_filings(320193, client=sec.client)
    assert [f.accession for f in found] == [
        "0000320193-26-000010",
        "0000320193-22-000078",
        "0000320193-15-000001",
    ]
    newest = found[0]
    assert newest.form == "4" and newest.primary_document == "form4.xml"  # xslF345X06/ removed
    assert newest.accepted_at == datetime(2026, 9, 1, 21, 0)
    assert newest.report_date == date(2026, 8, 30)
    assert newest.url == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/form4.xml"
    assert found[1].form == "4/A"


def test_list_filters_by_filing_date_and_skips_pages_outside_the_range():
    sec = FakeSec()
    found = list_form4_filings(320193, since=date(2022, 1, 1), until=date(2026, 8, 31), client=sec.client)
    assert [f.accession for f in found] == ["0000320193-22-000078"]
    assert not any("submissions-001" in u for u in sec.urls)  # the 2010-2016 page was never fetched
    # A request that reaches into 2015 does fetch it.
    sec2 = FakeSec()
    list_form4_filings(320193, since=date(2015, 1, 1), client=sec2.client)
    assert any("submissions-001" in u for u in sec2.urls)


def test_resolve_cik_uses_the_ticker_file_once():
    sec = FakeSec()
    assert sec_form4.resolve_cik("aapl", client=sec.client) == 320193
    assert sec_form4.resolve_cik("ZZZZ", client=sec.client) is None
    assert sum(u == sec_form4.TICKER_MAP_URL for u in sec.urls) == 1


# ---- ingest ----


def facts(session):
    return list(session.exec(select(KnownFact).where(KnownFact.kind == FactKind.INSIDER_TRADE)).all())


def test_known_at_is_the_acceptance_time_and_effective_at_the_trade_date(session):
    result = ingest_form4_filing(session, "UNH", filing_of("unh_000146"), xml=xml_of("unh_000146"))
    assert (result.rows, result.created, result.already_known) == (1, 1, 0)
    (fact,) = facts(session)
    assert fact.known_at == datetime(2025, 5, 16, 22, 11, 47)
    assert fact.known_at_basis == "source"
    assert fact.effective_at == datetime(2025, 5, 16)
    assert fact.symbol == "UNH" and fact.source == "sec_edgar"
    assert fact.dedupe_key == "form4|0000731766-25-000146|0"
    p = fact.payload
    assert (p["code"], p["code_meaning"], p["shares"], p["price"]) == (
        "P",
        "Open-market or private purchase",
        17175.0,
        291.1161,
    )
    assert p["value"] == pytest.approx(17175 * 291.1161)
    assert (p["owner_name"], p["owner_cik"], p["is_officer"], p["is_director"], p["officer_title"]) == (
        "REX JOHN F",
        "0001676672",
        True,
        False,
        "President & CFO",
    )
    assert p["filing_url"].endswith("/000073176625000146/wk-form4_1747433501.xml")
    assert fact.source_ref == p["filing_url"]


def test_a_trade_made_before_the_filing_was_public_keeps_both_dates(session):
    # Noseworthy bought on 14 May; the filing was accepted on 16 May.
    ingest_form4_filing(session, "UNH", filing_of("unh_000140"), xml=xml_of("unh_000140"))
    (fact,) = facts(session)
    assert fact.effective_at == datetime(2025, 5, 14)
    assert fact.known_at == datetime(2025, 5, 16, 16, 22, 34)


def test_ingest_is_idempotent(session):
    for _ in range(3):
        ingest_form4_filing(session, "AAPL", filing_of("aapl_013191"), xml=xml_of("aapl_013191"))
    assert len(facts(session)) == 5  # 2 non-derivative + 3 derivative rows, the holding row skipped
    again = ingest_form4_filing(session, "AAPL", filing_of("aapl_013191"), xml=xml_of("aapl_013191"))
    assert (again.created, again.already_known) == (0, 5)


def test_amendment_rows_are_separate_facts_with_the_amendment_link(session):
    ingest_form4_filing(session, "AAPL", filing_of("aapl_orig"), xml=xml_of("aapl_orig"))
    ingest_form4_filing(session, "AAPL", filing_of("aapl_4a"), xml=xml_of("aapl_4a"))
    rows = sorted(facts(session), key=lambda f: f.dedupe_key)
    assert [f.dedupe_key for f in rows] == [
        "form4|0000320193-22-000076|0",
        "form4|0000320193-22-000076|1",
        "form4|0000320193-22-000078|0",
        "form4|0000320193-22-000078|1",
    ]
    amended = rows[2].payload
    assert amended["is_amendment"] and amended["form"] == "4/A"
    assert amended["date_of_original_submission"] == "2022-08-19"
    assert rows[2].known_at == datetime(2022, 8, 22, 22, 43, 18) > rows[0].known_at


def test_ingest_fetches_through_the_client(session):
    f = filing_of("unh_000140")
    sec = FakeSec({f.url: xml_of("unh_000140")})
    ingest_form4_filing(session, "UNH", f, client=sec.client)
    assert sec.urls == [f.url] and len(facts(session)) == 1


# ---- backfill ----


def aapl_docs():
    out = {}
    for accession, document, name in [
        ("0000320193-26-000010", "form4.xml", "aapl_013191"),
        ("0000320193-22-000078", "wf-form4a.xml", "aapl_4a"),
        ("0000320193-15-000001", "doc4.xml", "aapl_orig"),
    ]:
        url = sec_form4.ARCHIVE_URL.format(cik=320193, accession=accession.replace("-", ""), document=document)
        out[url] = xml_of(name)
    return out


def test_backfill_is_resumable_and_reports_progress(session):
    docs = aapl_docs()
    sec = FakeSec(docs)
    broken = next(u for u in docs if "000032019322000078" in u)
    sec.fail.add(broken)
    seen = []
    first = backfill_insider_trades(session, ["aapl"], date(2010, 1, 1), client=sec.client, progress=seen.append)
    assert first.filings_seen == 3 and first.filings_ingested == 2
    assert len(first.errors) == 1 and "0000320193-22-000078" in first.errors[0]
    assert [p.filings_done for p in seen] == [1, 2, 3] and seen[-1].errors == 1
    stored_after_first = len(facts(session))
    assert stored_after_first == 2 + 5  # aapl_orig (2 rows) + aapl_013191 (5 rows)

    # Second run: the document works now; only the missing filing is fetched.
    sec.fail.clear()
    sec.urls.clear()
    second = backfill_insider_trades(session, ["AAPL"], date(2010, 1, 1), client=sec.client)
    assert second.filings_skipped_existing == 2 and second.filings_ingested == 1 and not second.errors
    assert [u for u in sec.urls if "/Archives/" in u] == [broken]
    assert len(facts(session)) == stored_after_first + 2

    # Third run changes nothing and fetches no document.
    sec.urls.clear()
    third = backfill_insider_trades(session, ["AAPL"], date(2010, 1, 1), client=sec.client)
    assert third.filings_ingested == 0 and third.facts_created == 0
    assert not any("/Archives/" in u for u in sec.urls)


def test_backfill_reports_unknown_symbols_and_keeps_going(session):
    sec = FakeSec(aapl_docs())
    report = backfill_insider_trades(session, ["NOPE", "AAPL"], date(2026, 1, 1), client=sec.client)
    assert report.unknown_symbols == ["NOPE"] and report.filings_ingested == 1


def test_fetch_new_returns_only_rows_created_by_this_call(session):
    sec = FakeSec(aapl_docs())
    first = fetch_new_insider_trades(session, "AAPL", today=date(2026, 9, 5), client=sec.client)
    assert first.latest_accession == "0000320193-26-000010"
    assert len(first.new_facts) == 5 and first.filings_checked == 1
    again = fetch_new_insider_trades(
        session, "AAPL", since_accession=first.latest_accession, today=date(2026, 9, 5), client=sec.client
    )
    assert again.new_facts == [] and again.filings_checked == 0
