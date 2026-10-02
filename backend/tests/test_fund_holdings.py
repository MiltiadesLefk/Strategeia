"""13F fund holdings: reading the information table, combining lots, value units,
matching issuers to tickers by name, storing dated facts, quarter-over-quarter
changes, amendments, and what is visible at a given moment. Built from small
synthetic filings in the real EDGAR shape (tests/fund_helpers.py); no network."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.data_providers import sec_13f
from app.data_providers.base import DataProviderError
from app.data_providers.sec_13f import (
    Filing13F,
    IssuerMatcher,
    backfill_13f,
    combine_holdings,
    fetch_new_13f,
    infer_value_unit,
    ingest_13f_filing,
    list_13f_filings,
    normalise_issuer_name,
    parse_13f_cover,
    parse_information_table,
)
from app.knowledge import FactKind, KnownFact, as_of
from app.knowledge.fund_holdings import (
    STATUS_ADDED,
    STATUS_NEW,
    STATUS_SOLD_OUT,
    STATUS_TRIMMED,
    STATUS_UNCHANGED,
    classify_change,
    fund_changes_as_of,
    fund_filings_as_of,
    fund_holders_of_symbol,
    fund_positions_as_of,
)
from tests.fund_helpers import (
    FUND,
    OTHER_FUND,
    Q1_ACCEPTED,
    Q1_ROWS,
    Q2_ROWS,
    TABLE_NS,
    FakeFunds,
    cover_xml,
    info_row,
    matcher,
    serve_two_quarters,
    table_xml,
    when,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def loaded(session) -> FakeFunds:
    sec = FakeFunds()
    serve_two_quarters(sec)
    backfill_13f(session, [FUND], 4, client=sec.client, matcher=matcher())
    return sec


# ------------------------------------------------------------------ the information table


def test_table_rows_are_read_with_the_filers_namespace():
    rows = parse_information_table(table_xml(info_row("APPLE INC", "037833100", 200000, 1000), info_row("X", "1", 5, 6, put_call="Call")))
    assert [(r.cusip, r.value_raw, r.shares, r.share_type, r.put_call) for r in rows] == [
        ("037833100", 200000.0, 1000.0, "SH", None),
        ("1", 5.0, 6.0, "SH", "Call"),
    ]


def test_a_filer_that_prefixes_every_tag_still_parses():
    xml = (
        f'<ns1:informationTable xmlns:ns1="{TABLE_NS}"><ns1:infoTable><ns1:nameOfIssuer>APPLE INC</ns1:nameOfIssuer>'
        "<ns1:cusip>037833100</ns1:cusip><ns1:value>10</ns1:value><ns1:shrsOrPrnAmt><ns1:sshPrnamt>2</ns1:sshPrnamt>"
        "<ns1:sshPrnamtType>SH</ns1:sshPrnamtType></ns1:shrsOrPrnAmt></ns1:infoTable></ns1:informationTable>"
    )
    (row,) = parse_information_table(xml)
    assert row.issuer == "APPLE INC" and row.shares == 2.0


def test_a_row_without_a_cusip_or_count_is_skipped_not_guessed():
    xml = table_xml(info_row("NO CUSIP", "", 10, 5), "<infoTable><cusip>1</cusip><value>1</value></infoTable>", info_row("OK", "2", 3, 4))
    assert [r.cusip for r in parse_information_table(xml)] == ["2"]


def test_doctype_and_entities_are_refused():
    bomb = b'<!DOCTYPE x [<!ENTITY a "aaaa">]><informationTable/>'
    with pytest.raises(DataProviderError):
        parse_information_table(bomb)
    with pytest.raises(DataProviderError):
        parse_13f_cover(bomb)


def test_the_wrong_document_is_an_error_not_an_empty_table():
    with pytest.raises(DataProviderError):
        parse_information_table(b"<other/>")
    with pytest.raises(DataProviderError):
        parse_information_table(b"not xml")


def test_cover_page_period_manager_and_amendment_kind():
    cover = parse_13f_cover(cover_xml("06-30-2025", "Some Capital", amendment="NEW HOLDINGS", entries=12, total=999))
    assert cover.period == date(2025, 6, 30) and cover.manager_name == "Some Capital"
    assert cover.is_amendment and cover.amendment_type == "NEW HOLDINGS"
    assert cover.table_entry_total == 12 and cover.table_value_total == 999.0
    original = parse_13f_cover(cover_xml())
    assert not original.is_amendment and original.amendment_type is None


# ------------------------------------------------------------------ lots and units


def test_lots_of_one_security_are_added_together_but_options_and_bonds_stay_apart():
    rows = parse_information_table(
        table_xml(
            info_row("APPLE INC", "037833100", 100, 10, discretion="SOLE"),
            info_row("APPLE INC", "037833100", 300, 30, discretion="DFND", other="2"),
            info_row("APPLE INC", "037833100", 50, 5, put_call="Put"),
            info_row("APPLE INC", "037833100", 70, 7, put_call="Call"),
            info_row("APPLE INC", "037833100", 9, 9, kind="PRN"),
        )
    )
    holdings = {(h.put_call, h.share_type): h for h in combine_holdings(rows)}
    assert len(holdings) == 4
    stock = holdings[(None, "SH")]
    assert (stock.shares, stock.value_raw, stock.row_count, stock.discretion) == (40.0, 400.0, 2, "MIXED")
    assert stock.other_managers == ("2",)
    assert holdings[("Put", "SH")].shares == 5.0 and holdings[("Call", "SH")].shares == 7.0
    assert holdings[(None, "PRN")].shares == 9.0


def holding(value, shares, **kw):
    return combine_holdings(parse_information_table(table_xml(info_row("X", kw.pop("cusip", "1"), value, shares, **kw))))[0]


def test_values_before_2023_are_thousands_by_the_forms_own_rule():
    unit = infer_value_unit(date(2022, 9, 30), [holding(150, 1)])
    assert (unit.multiplier, unit.name, unit.how) == (1000, "thousands", "period")


def test_values_from_2023_are_dollars_when_the_implied_price_is_a_stock_price():
    unit = infer_value_unit(date(2025, 6, 30), [holding(15000, 100), holding(4000, 50, cusip="2")])
    assert (unit.multiplier, unit.name, unit.how) == (1, "dollars", "implied_price")


def test_a_manager_still_reporting_thousands_after_2022_is_caught_by_the_implied_price():
    # Values in thousands read as dollars give a few tenths of a dollar a share.
    unit = infer_value_unit(date(2026, 6, 30), [holding(15, 100), holding(40, 200, cusip="2"), holding(8, 50, cusip="3")])
    assert (unit.multiplier, unit.name, unit.how) == (1000, "thousands", "implied_price")


def test_options_and_bond_principal_do_not_decide_the_unit():
    rows = [holding(15000, 100), holding(1, 100000, put_call="Call", cusip="2"), holding(5, 5000, kind="PRN", cusip="3")]
    assert infer_value_unit(date(2025, 6, 30), rows).name == "dollars"
    assert infer_value_unit(date(2025, 6, 30), []).how == "no_usable_rows"


# ------------------------------------------------------------------ names to tickers


def test_name_normalisation_drops_legal_suffixes_and_spells_out_abbreviations():
    assert normalise_issuer_name("Apple Inc.") == normalise_issuer_name("APPLE INC") == ("APPLE", None)
    assert normalise_issuer_name("THE COCA COLA CO")[0] == "COCA COLA"
    assert normalise_issuer_name("Alphabet Inc. (Class A)") == ("ALPHABET", "A")
    assert normalise_issuer_name("ALPHABET INC", "CAP STK CL C") == ("ALPHABET", "C")
    assert normalise_issuer_name("Acme Hldgs Intl Corp")[0] == "ACME HOLDINGS INTERNATIONAL"


def test_exact_normalised_names_match_and_everything_else_stays_unmatched():
    m = matcher()
    assert m.match("037833100", "APPLE INC") == "AAPL"
    assert m.match("594918104", "MICROSOFT CORP") == "MSFT"
    assert m.match("191216100", "COCA COLA CO") == "KO"
    assert m.match("x", "AMAZON COM INC") == "AMZN"
    assert m.match("x", "MICROSOFT") == "MSFT"  # a missing legal suffix is still the same name
    # Near misses are not matches: no fuzzy guessing.
    assert m.match("x", "APPLE HOSPITALITY REIT INC") is None
    assert m.match("x", "TOTALLY UNKNOWN CORP") is None


def test_two_companies_with_one_name_are_never_guessed():
    m = IssuerMatcher([("ACM1", "Acme Corp"), ("ACM2", "Acme Inc")])
    assert m.match("x", "ACME CORP") is None


def test_share_classes_must_line_up_and_known_cusips_decide_identical_names():
    m = IssuerMatcher([("FOOB", "Foo Inc Class B")])
    assert m.match("x", "FOO INC", "CL B") == "FOOB"
    assert m.match("x", "FOO INC", "CL A") is None
    assert m.match("084670702", "BERKSHIRE HATHAWAY INC DEL", "CL B NEW") == "BRK-B"
    assert m.match("02079K305", "ALPHABET INC") == "GOOGL"


def test_the_second_name_source_only_fills_gaps_it_never_overrides():
    m = IssuerMatcher([("AAPL", "Apple Inc.")], extra=[("APLE", "Apple Inc"), ("ZZZ", "Zeta Corp")])
    assert m.match("x", "APPLE INC") == "AAPL"
    assert m.match("x", "ZETA CORP") == "ZZZ"


# ------------------------------------------------------------------ the filing list


def test_listing_keeps_13f_forms_notices_included_and_strips_the_stylesheet_folder():
    from tests.test_sec_8k import submissions

    sec = FakeFunds()
    sec.serve_13f(FUND, "0000000001-25-000001", Q1_ACCEPTED, "2025-03-31", Q1_ROWS)
    sec.serve_13f(FUND, "0000000001-25-000003", "2025-09-01T10:00:00.000Z", "2025-06-30", [], form="13F-NT")
    sec._rows[FUND].append(
        {"accession": "0000000001-25-000009", "accepted": "2025-09-02T10:00:00.000Z", "form": "8-K", "filed": "2025-09-02", "report": "", "doc": "x.htm", "items": ""}
    )
    sec.submissions[FUND] = {**submissions(sec._rows[FUND]), "name": "Test Fund LP"}
    filings, name = list_13f_filings(FUND, client=sec.client)
    assert name == "Test Fund LP"
    assert [f.form for f in filings] == ["13F-NT", "13F-HR"]  # newest first, the 8-K is gone
    assert filings[1].primary_document == "primary_doc.xml"
    assert filings[1].report_date == date(2025, 3, 31) and filings[1].accepted_at == when("2025-05-15T21:00:00")
    only_reports, _ = list_13f_filings(FUND, client=sec.client, include_notices=False)
    assert [f.form for f in only_reports] == ["13F-HR"]
    recent, _ = list_13f_filings(FUND, since=date(2025, 6, 1), client=sec.client)
    assert [f.form for f in recent] == ["13F-NT"]


# ------------------------------------------------------------------ storing


def filing_of(sec, cik, accession) -> Filing13F:
    filings, _ = list_13f_filings(cik, client=sec.client)
    return next(f for f in filings if f.accession == accession)


def test_a_filing_is_stored_with_its_acceptance_time_and_the_quarter_end(session):
    sec = FakeFunds()
    serve_two_quarters(sec)
    result = ingest_13f_filing(
        session, filing_of(sec, FUND, "0000000001-25-000002"), client=sec.client, matcher=matcher(), manager_name="Test Fund LP"
    )
    assert (result.holdings, result.created, result.matched, result.unit) == (4, 4, 4, "dollars")  # the two Apple lots are one holding
    facts = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.FUND_HOLDING)).all()
    assert {f.known_at for f in facts} == {when("2025-08-14T21:00:00")}
    assert {f.effective_at for f in facts} == {datetime(2025, 6, 30)}
    assert {f.known_at_basis for f in facts} == {"source"}
    apple = next(f for f in facts if f.symbol == "AAPL")
    assert apple.payload["shares"] == 1500.0 and apple.payload["value"] == 300000.0 and apple.payload["row_count"] == 2
    assert apple.payload["cik"] == str(FUND) and apple.payload["period"] == "2025-06-30"
    (filing_fact,) = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.FUND_FILING)).all()
    assert filing_fact.payload["holdings_count"] == 4 and filing_fact.payload["matched_count"] == 4
    assert filing_fact.payload["total_value"] == 590000.0


def test_loading_a_filing_twice_stores_nothing_new(session):
    sec = FakeFunds()
    serve_two_quarters(sec)
    filing = filing_of(sec, FUND, "0000000001-25-000001")
    ingest_13f_filing(session, filing, client=sec.client, matcher=matcher())
    again = ingest_13f_filing(session, filing, client=sec.client, matcher=matcher())
    assert (again.created, again.already_known, again.is_new_filing) == (0, 4, False)
    assert len(session.exec(select(KnownFact)).all()) == 5


def test_thousands_before_2023_become_dollars_in_the_stored_value(session):
    sec = FakeFunds()
    sec.serve_13f(FUND, "0000000001-22-000001", "2022-11-14T21:00:00.000Z", "2022-09-30", [info_row("APPLE INC", "037833100", 150, 1)])
    ingest_13f_filing(session, filing_of(sec, FUND, "0000000001-22-000001"), client=sec.client, matcher=matcher())
    fact = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.FUND_HOLDING)).one()
    assert fact.payload["value"] == 150000.0 and fact.payload["value_raw"] == 150.0 and fact.payload["value_unit"] == "thousands"


def test_an_unmatched_issuer_keeps_no_ticker_and_bonds_never_get_one(session):
    sec = FakeFunds()
    sec.serve_13f(
        FUND,
        "0000000001-25-000001",
        Q1_ACCEPTED,
        "2025-03-31",
        [info_row("MYSTERY HOLDINGS CO", "999999999", 5000, 100), info_row("APPLE INC", "037833100", 900, 900, kind="PRN")],
    )
    ingest_13f_filing(session, filing_of(sec, FUND, "0000000001-25-000001"), client=sec.client, matcher=matcher())
    facts = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.FUND_HOLDING)).all()
    assert [f.symbol for f in facts] == [None, None]


def test_a_notice_stores_only_its_filing_fact(session):
    sec = FakeFunds()
    sec.serve_13f(FUND, "0000000001-25-000005", "2025-08-14T12:00:00.000Z", "2025-06-30", [], form="13F-NT")
    result = ingest_13f_filing(session, filing_of(sec, FUND, "0000000001-25-000005"), client=sec.client, matcher=matcher())
    assert result.holdings == 0
    assert session.exec(select(KnownFact).where(KnownFact.kind == FactKind.FUND_HOLDING)).all() == []
    (fact,) = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.FUND_FILING)).all()
    assert fact.payload["is_notice"] is True
    assert fund_positions_as_of(session, FUND) == (None, [])  # a notice has no holdings of its own


def test_a_filing_with_no_table_document_fails_cleanly(session):
    sec = FakeFunds()
    directory = sec.serve_13f(FUND, "0000000001-25-000001", Q1_ACCEPTED, "2025-03-31", Q1_ROWS)
    sec.documents[f"{directory}/index.json"] = b'{"directory": {"item": [{"name": "primary_doc.xml"}]}}'
    with pytest.raises(DataProviderError):
        ingest_13f_filing(session, filing_of(sec, FUND, "0000000001-25-000001"), client=sec.client, matcher=matcher())


# ------------------------------------------------------------------ backfill


def test_backfill_takes_the_latest_quarters_and_a_rerun_makes_no_document_requests(session):
    sec = FakeFunds()
    serve_two_quarters(sec)
    first = backfill_13f(session, [FUND], 1, client=sec.client, matcher=matcher())
    assert (first.filings_ingested, first.holdings_created, first.errors) == (1, 4, [])
    assert [f.period for f in fund_filings_as_of(session)] == [date(2025, 6, 30)]
    second = backfill_13f(session, [FUND], 4, client=sec.client, matcher=matcher())
    assert (second.filings_ingested, second.filings_skipped_existing) == (1, 1)  # Q2 is skipped, Q1 is loaded
    sec.urls.clear()
    third = backfill_13f(session, [FUND], 4, client=sec.client, matcher=matcher())
    assert third.filings_ingested == 0 and third.filings_skipped_existing == 2
    assert not any("infotable" in u or "primary_doc" in u for u in sec.urls)


def test_backfill_reports_a_failing_fund_and_carries_on(session):
    sec = FakeFunds()
    serve_two_quarters(sec, OTHER_FUND)  # FUND has no submissions at all: SEC answers 404
    report = backfill_13f(session, [FUND, OTHER_FUND, "0", "x"], 2, client=sec.client, matcher=matcher())
    assert report.funds == 2 and report.filings_ingested == 2
    assert len(report.errors) == 1 and f"CIK {FUND}" in report.errors[0]


def test_a_broken_filing_is_reported_and_the_others_still_load(session):
    sec = FakeFunds()
    serve_two_quarters(sec)
    directory = sec_13f.ARCHIVE_DIR_URL.format(cik=FUND, accession_nodash="000000000125000001")
    sec.documents[f"{directory}/infotable.xml"] = b"<broken"
    report = backfill_13f(session, [FUND], 4, client=sec.client, matcher=matcher())
    assert report.filings_ingested == 1 and len(report.errors) == 1 and "0000000001-25-000001" in report.errors[0]


def test_fetch_new_only_loads_filings_not_stored_yet_and_raises_when_sec_refuses(session):
    sec = FakeFunds()
    serve_two_quarters(sec)
    fetched = fetch_new_13f(session, FUND, client=sec.client, matcher=matcher(), today=date(2025, 9, 1))
    assert [r.is_new_filing for r in fetched.results] == [True, True] and fetched.manager_name == "Test Fund LP"
    again = fetch_new_13f(session, FUND, client=sec.client, matcher=matcher(), today=date(2025, 9, 1))
    assert again.results == []
    refused = FakeFunds()
    refused.serve_13f(OTHER_FUND, "0000000002-25-000001", Q1_ACCEPTED, "2025-03-31", Q1_ROWS)
    refused.status["infotable.xml"] = 429
    with pytest.raises(DataProviderError):
        fetch_new_13f(session, OTHER_FUND, client=refused.client, matcher=matcher(), today=date(2025, 9, 1))


# ------------------------------------------------------------------ the changes between quarters


def test_classification_thresholds():
    assert classify_change(0, 10) == (STATUS_NEW, None)
    assert classify_change(10, 0) == (STATUS_SOLD_OUT, -100.0)
    assert classify_change(100, 150) == (STATUS_ADDED, 50.0)
    assert classify_change(100, 60) == (STATUS_TRIMMED, -40.0)
    assert classify_change(100, 100.5)[0] == STATUS_UNCHANGED  # under 1%: a rounding, not a decision
    assert classify_change(0, 0) == (STATUS_UNCHANGED, 0.0)


def test_changes_between_the_two_latest_quarters(session):
    loaded(session)
    result = fund_changes_as_of(session, FUND)
    assert result.period == date(2025, 6, 30) and result.previous_period == date(2025, 3, 31)
    assert result.consecutive and result.has_comparison and result.holdings_count == 4
    by_symbol = {c.symbol: c for c in result.changes}
    assert by_symbol["AAPL"].status == STATUS_ADDED and by_symbol["AAPL"].change_pct == 50.0
    assert by_symbol["MSFT"].status == STATUS_TRIMMED and by_symbol["MSFT"].change_pct == -40.0
    assert by_symbol["KO"].status == STATUS_UNCHANGED
    assert by_symbol["NVDA"].status == STATUS_NEW and by_symbol["NVDA"].change_pct is None
    assert by_symbol["AMZN"].status == STATUS_SOLD_OUT and by_symbol["AMZN"].shares_now == 0.0
    assert [result.count(s) for s in (STATUS_NEW, STATUS_ADDED, STATUS_TRIMMED, STATUS_SOLD_OUT)] == [1, 1, 1, 1]
    assert result.filed_at == when("2025-08-14T21:00:00") and result.previous_filed_at == when("2025-05-15T21:00:00")
    # Weights are shares of the reported portfolio in each quarter (590,000 now).
    assert by_symbol["AAPL"].weight_now_pct == pytest.approx(300000 / 590000 * 100, rel=1e-6)
    assert by_symbol["AMZN"].weight_now_pct == 0.0 and by_symbol["AMZN"].weight_before_pct > 0
    assert result.changes[0].symbol == "AAPL"  # largest first


def test_one_quarter_alone_has_holdings_but_no_comparison(session):
    sec = FakeFunds()
    sec.serve_13f(FUND, "0000000001-25-000001", Q1_ACCEPTED, "2025-03-31", Q1_ROWS)
    backfill_13f(session, [FUND], 4, client=sec.client, matcher=matcher())
    result = fund_changes_as_of(session, FUND)
    assert not result.has_comparison and result.changes == [] and result.holdings_count == 4
    assert fund_changes_as_of(session, OTHER_FUND) is None


def test_a_skipped_quarter_is_not_called_consecutive(session):
    sec = FakeFunds()
    sec.serve_13f(FUND, "0000000001-25-000001", Q1_ACCEPTED, "2025-03-31", Q1_ROWS)
    sec.serve_13f(FUND, "0000000001-25-000007", "2025-11-14T21:00:00.000Z", "2025-09-30", Q2_ROWS)
    backfill_13f(session, [FUND], 4, client=sec.client, matcher=matcher())
    assert fund_changes_as_of(session, FUND).consecutive is False


# ------------------------------------------------------------------ what was known when


def test_a_quarter_filed_after_the_cutoff_does_not_exist_yet(session):
    loaded(session)
    before_q2 = when("2025-08-14T20:59:59")
    result = fund_changes_as_of(session, FUND, before_q2)
    assert result.period == date(2025, 3, 31) and not result.has_comparison and result.holdings_count == 4
    filing, positions = fund_positions_as_of(session, FUND, before_q2)
    assert filing.period == date(2025, 3, 31) and {p.symbol for p in positions} == {"AAPL", "MSFT", "KO", "AMZN"}
    assert fund_changes_as_of(session, FUND, when("2025-05-15T20:59:59")) is None
    assert fund_holders_of_symbol(session, "NVDA", before_q2) == []  # bought in Q2, filed in August
    with as_of(before_q2):
        assert fund_changes_as_of(session, FUND).period == date(2025, 3, 31)
    assert fund_changes_as_of(session, FUND, when("2025-08-14T21:00:00")).period == date(2025, 6, 30)  # inclusive cutoff


def test_holders_of_a_symbol_with_status_and_sold_out_funds(session):
    sec = loaded(session)
    # A second fund holds Apple in Q1 and Q2 but sold Microsoft; it files later.
    sec.serve_13f(
        OTHER_FUND,
        "0000000002-25-000001",
        "2025-05-16T21:00:00.000Z",
        "2025-03-31",
        [info_row("APPLE INC", "037833100", 100000, 500), info_row("MICROSOFT CORP", "594918104", 40000, 100)],
    )
    sec.serve_13f(OTHER_FUND, "0000000002-25-000002", "2025-08-20T21:00:00.000Z", "2025-06-30", [info_row("APPLE INC", "037833100", 100000, 500)])
    backfill_13f(session, [OTHER_FUND], 4, client=sec.client, matcher=matcher())
    aapl = {h.cik: h for h in fund_holders_of_symbol(session, "AAPL")}
    mine = aapl[str(FUND)]
    assert (mine.status, mine.shares, mine.previous_shares) == (STATUS_ADDED, 1500.0, 1000.0)
    assert mine.weight_pct == pytest.approx(300000 / 590000 * 100, rel=1e-6)
    assert aapl[str(OTHER_FUND)].status == STATUS_UNCHANGED
    msft = {h.cik: h.status for h in fund_holders_of_symbol(session, "MSFT")}
    assert msft == {str(FUND): STATUS_TRIMMED, str(OTHER_FUND): STATUS_SOLD_OUT}
    assert [(h.cik, h.status) for h in fund_holders_of_symbol(session, "NVDA")] == [(str(FUND), STATUS_NEW)]
    assert [h.cik for h in fund_holders_of_symbol(session, "AAPL", ciks=[FUND])] == [str(FUND)]
    assert fund_holders_of_symbol(session, "ZZZZ") == []


# ------------------------------------------------------------------ amendments


def amended_world(session, kind):
    sec = FakeFunds()
    sec.serve_13f(FUND, "0000000001-25-000001", Q1_ACCEPTED, "2025-03-31", Q1_ROWS)
    rows = [info_row("NVIDIA CORP", "67066G104", 70000, 700)] if kind == "NEW HOLDINGS" else [info_row("APPLE INC", "037833100", 100000, 500)]
    sec.serve_13f(FUND, "0000000001-25-000008", "2025-06-10T21:00:00.000Z", "2025-03-31", rows, form="13F-HR/A", amendment=kind)
    backfill_13f(session, [FUND], 4, client=sec.client, matcher=matcher())


def test_a_restatement_replaces_the_quarter_only_from_the_day_it_was_accepted(session):
    amended_world(session, "RESTATEMENT")
    _, before = fund_positions_as_of(session, FUND, when("2025-06-09T00:00:00"))
    assert {p.symbol for p in before} == {"AAPL", "MSFT", "KO", "AMZN"}
    filing, after = fund_positions_as_of(session, FUND, when("2025-06-11T00:00:00"))
    assert [(p.symbol, p.shares) for p in after] == [("AAPL", 500.0)] and filing.is_amendment


def test_a_new_holdings_amendment_adds_to_the_original(session):
    amended_world(session, "NEW HOLDINGS")
    _, before = fund_positions_as_of(session, FUND, when("2025-06-09T00:00:00"))
    assert len(before) == 4
    _, after = fund_positions_as_of(session, FUND, when("2025-06-11T00:00:00"))
    assert {p.symbol for p in after} == {"AAPL", "MSFT", "KO", "AMZN", "NVDA"}


def test_an_amendment_without_a_kind_is_read_as_a_restatement(session):
    sec = FakeFunds()
    sec.serve_13f(FUND, "0000000001-25-000001", Q1_ACCEPTED, "2025-03-31", Q1_ROWS)
    sec.serve_13f(
        FUND, "0000000001-25-000008", "2025-06-10T21:00:00.000Z", "2025-03-31", [info_row("APPLE INC", "037833100", 100000, 500)], form="13F-HR/A", amendment="UNKNOWN"
    )
    backfill_13f(session, [FUND], 4, client=sec.client, matcher=matcher())
    assert [p.symbol for p in fund_positions_as_of(session, FUND)[1]] == ["AAPL"]
