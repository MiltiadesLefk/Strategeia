"""Schedule 13D/13G (5% owners), the two silent signals, the fund filings watcher,
the Funds service and its endpoints. 13D/13G documents come from recorded filings
in tests/fixtures/funds; 13F filings are built in tests/fund_helpers.py. No network."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis import fund_signals
from app.analysis.shadow_signals import ShadowContext, registered_shadow_signals
from app.api.deps import get_app_settings, get_session
from app.config import AppSettings
from app.data_providers import sec_13dg, sec_form4
from app.data_providers.base import DataProviderError
from app.data_providers.sec_13dg import (
    OwnershipFiling,
    fetch_ownership_filings,
    ingest_ownership_filing,
    list_ownership_filings,
    parse_ownership_xml,
    ticker_for_cik,
)
from app.data_providers.sec_13f import backfill_13f
from app.knowledge import FactKind, KnownFact, as_of
from app.knowledge.fund_holdings import ownership_filings_as_of
from app.main import app
from app.services import funds_service
from app.timeutil import utcnow_naive
from app.watchers import fund_watcher, registry
from app.watchers.base import WatcherContext
from app.watchers.fund_watcher import FundWatcher, parse_ownership_feed, register_fund_watcher
from app.watchers.runner import run_watcher_once
from tests.fund_helpers import FUND, Q1_ACCEPTED, Q1_ROWS, FakeFunds, matcher, serve_two_quarters, when
from tests.test_sec_8k import submissions

FIXTURES = Path(__file__).parent / "fixtures" / "funds"
G_XML = (FIXTURES / "schedule_13g.xml").read_bytes()
D_AMENDMENT_XML = (FIXTURES / "schedule_13d_amendment.xml").read_bytes()
# A first-time 13D: the amendment recorded above with the amendment markers taken out.
D_XML = D_AMENDMENT_XML.replace(b"SCHEDULE 13D/A", b"SCHEDULE 13D").replace(
    b"<previousAccessionNumber>0001193125-19-306193</previousAccessionNumber>", b""
)
G_AMENDMENT_XML = G_XML.replace(b"SCHEDULE 13G<", b"SCHEDULE 13G/A<")
AAPL, NVDA, HHH = 320193, 1045810, 1981792
NOW = datetime(2025, 8, 15, 12, 0)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def _fresh_ticker_maps(monkeypatch):
    monkeypatch.setattr(sec_form4, "_ticker_map", None)
    sec_13dg.reset_cik_ticker_cache()


def filing_row(accession, accepted, form, doc="xslSCHEDULE_13G_X01/primary_doc.xml") -> dict:
    return {"accession": accession, "accepted": accepted, "form": form, "filed": accepted[:10], "report": "", "doc": doc, "items": ""}


def serve_ownership(sec: FakeFunds, cik: int, *entries) -> None:
    """entries: (accession, accepted, form, xml). The company's own folder holds each document."""
    rows = []
    for accession, accepted, form, xml in entries:
        rows.append(filing_row(accession, accepted, form))
        directory = sec_13dg.ARCHIVE_DIR_URL.format(cik=cik, accession_nodash=accession.replace("-", ""))
        sec.documents[f"{directory}/primary_doc.xml"] = xml
    sec.submissions[cik] = submissions(rows)


def stored_filing(accession="0000000009-26-000001", accepted=datetime(2026, 9, 20, 21, 0), form="SCHEDULE 13G", cik=AAPL) -> OwnershipFiling:
    return OwnershipFiling(cik, accession, form, accepted.date(), accepted, "primary_doc.xml")


# ------------------------------------------------------------------ parsing


def test_a_13g_gives_the_holder_the_percent_and_the_shares():
    doc = parse_ownership_xml(G_XML)
    assert (doc.schedule, doc.is_amendment, doc.purpose) == ("13G", False, None)
    assert doc.issuer_name == "Apple Inc" and doc.issuer_cik == "320193" and doc.issuer_cusip == "037833100"
    assert doc.event_date == date(2026, 3, 31) and doc.rule == "Rule 13d-1(b)" and doc.class_title == "Common Stock"
    head = doc.headline
    assert head.name == "Vanguard Capital Management" and head.percent == 7.48 and head.shares == 1099168953.0
    assert head.person_type == "IA"


def test_a_13d_amendment_keeps_every_reporting_person_and_the_purpose_text():
    doc = parse_ownership_xml(D_AMENDMENT_XML)
    assert doc.schedule == "13D" and doc.is_amendment and doc.amendment_no == 33
    assert doc.issuer_cik == str(HHH) and doc.event_date == date(2026, 6, 4)
    assert len(doc.persons) >= 2
    head = doc.headline
    assert head.name.startswith("Pershing Square Capital Management") and head.percent == 46.7 and head.shares == 27852064.0
    assert doc.purpose.startswith("Item 4 of the Schedule 13D is hereby amended")
    assert doc.previous_accession == "0001193125-19-306193"
    assert parse_ownership_xml(D_XML).is_amendment is False


def test_documents_that_are_not_structured_13d_or_13g_are_errors():
    with pytest.raises(DataProviderError):
        parse_ownership_xml(b"<html><body>SC 13G free text</body></html>")
    with pytest.raises(DataProviderError):
        parse_ownership_xml(b"<edgarSubmission><headerData><submissionType>4</submissionType></headerData></edgarSubmission>")
    with pytest.raises(DataProviderError):
        parse_ownership_xml(b'<!DOCTYPE x [<!ENTITY a "b">]><edgarSubmission/>')


# ------------------------------------------------------------------ storing and reading


def test_a_13g_is_stored_with_its_acceptance_time_and_the_event_date(session):
    result = ingest_ownership_filing(session, stored_filing(), "AAPL", xml=G_XML)
    assert result.created
    fact = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.OWNERSHIP_FILING)).one()
    assert fact.symbol == "AAPL" and fact.known_at == datetime(2026, 9, 20, 21, 0) and fact.known_at_basis == "source"
    assert fact.effective_at == datetime(2026, 3, 31)
    assert fact.payload["percent"] == 7.48 and fact.payload["filer_name"] == "Vanguard Capital Management"
    assert ingest_ownership_filing(session, stored_filing(), "AAPL", xml=G_XML).created is False
    assert len(session.exec(select(KnownFact)).all()) == 1


def test_readers_filter_by_window_schedule_symbol_and_what_was_known(session):
    ingest_ownership_filing(session, stored_filing("0000000009-26-000001", datetime(2026, 9, 20, 21, 0)), "AAPL", xml=G_XML)
    ingest_ownership_filing(
        session, stored_filing("0000000009-26-000002", datetime(2026, 9, 25, 21, 0), "SCHEDULE 13D", HHH), "HHH", xml=D_XML
    )
    ingest_ownership_filing(
        session, stored_filing("0000000009-26-000003", datetime(2026, 6, 1, 21, 0), "SCHEDULE 13G", AAPL), "AAPL", xml=G_XML
    )
    now = datetime(2026, 9, 28, 12, 0)
    everything = ownership_filings_as_of(session, as_of=now, window_days=90)
    assert [r.accession for r in everything] == ["0000000009-26-000002", "0000000009-26-000001"]  # newest first, June is outside
    assert [r.schedule for r in everything] == ["13D", "13G"]
    assert [r.accession for r in ownership_filings_as_of(session, "AAPL", as_of=now, window_days=365)] == [
        "0000000009-26-000001",
        "0000000009-26-000003",
    ]
    assert [r.symbol for r in ownership_filings_as_of(session, as_of=now, schedule="13D")] == ["HHH"]
    # A filing accepted after the cutoff does not exist at the cutoff.
    assert [r.accession for r in ownership_filings_as_of(session, as_of=datetime(2026, 9, 22, 0, 0))] == ["0000000009-26-000001"]
    with as_of(datetime(2026, 9, 21, 0, 0)):
        assert len(ownership_filings_as_of(session)) == 1
    record = everything[0]
    assert record.percent == 46.7 and record.filer_name.startswith("Pershing Square") and record.person_count >= 2


def test_the_listing_separates_structured_filings_from_the_old_free_text_ones():
    sec = FakeFunds()
    sec.submissions[AAPL] = submissions(
        [
            filing_row("0000000009-26-000004", "2026-09-22T21:00:00.000Z", "SCHEDULE 13G/A"),
            filing_row("0000000009-26-000005", "2026-09-21T21:00:00.000Z", "SC 13G"),
            filing_row("0000000009-26-000006", "2026-09-20T21:00:00.000Z", "8-K"),
            filing_row("0000000009-24-000001", "2024-02-14T21:00:00.000Z", "SCHEDULE 13G"),
        ]
    )
    listing = list_ownership_filings(AAPL, since=date(2026, 9, 1), client=sec.client)
    assert [f.form for f in listing.filings] == ["SCHEDULE 13G/A"] and listing.legacy_count == 1
    assert listing.filings[0].primary_document == "primary_doc.xml"  # the stylesheet folder is removed
    assert len(list_ownership_filings(AAPL, client=sec.client).filings) == 2


def test_fetching_a_companys_filings_stores_new_ones_once_and_skips_unreadable_ones(session):
    sec = FakeFunds()
    serve_ownership(
        sec,
        AAPL,
        ("0000000009-26-000010", "2026-09-22T21:00:00.000Z", "SCHEDULE 13G", G_XML),
        ("0000000009-26-000011", "2026-09-23T21:00:00.000Z", "SCHEDULE 13G", b"<html>not xml</html>"),
    )
    first = fetch_ownership_filings(session, AAPL, "AAPL", client=sec.client, today=date(2026, 9, 25))
    assert (first.filings_checked, len(first.new_facts), first.unreadable) == (2, 1, 1)
    second = fetch_ownership_filings(session, AAPL, "AAPL", client=sec.client, today=date(2026, 9, 25))
    assert second.new_facts == [] and second.unreadable == 1  # the unreadable one is retried, never stored
    sec.status["primary_doc.xml"] = 403
    with pytest.raises(DataProviderError):
        fetch_ownership_filings(session, AAPL, "AAPL", client=sec.client, today=date(2026, 9, 25), lookback_days=400)


def test_filings_by_a_fund_resolve_the_company_ticker_from_the_issuer_cik(session):
    sec = FakeFunds()
    sec.tickers = {"HHH": HHH}
    serve_ownership(sec, FUND, ("0000000009-26-000020", "2026-09-22T21:00:00.000Z", "SCHEDULE 13D", D_XML))
    fetched = fetch_ownership_filings(
        session, FUND, None, client=sec.client, today=date(2026, 9, 25), symbol_resolver=lambda c: ticker_for_cik(c, client=sec.client)
    )
    assert [f.symbol for f in fetched.new_facts] == ["HHH"]
    assert ticker_for_cik("0001981792", client=sec.client) == "HHH" and ticker_for_cik(99, client=sec.client) is None


# ------------------------------------------------------------------ the silent signals


def test_both_signals_are_registered_as_shadow_signals():
    assert {fund_signals.FUNDS_SIGNAL_NAME, fund_signals.OWNERSHIP_SIGNAL_NAME} <= set(registered_shadow_signals())


def loaded_funds(session) -> None:
    sec = FakeFunds()
    serve_two_quarters(sec)
    backfill_13f(session, [FUND], 4, client=sec.client, matcher=matcher())


def test_fund_accumulation_is_signed_by_direction(session):
    loaded_funds(session)
    with as_of(when("2025-09-01T00:00:00")):
        long_aapl = fund_signals.build_fund_accumulation_signal("long", session, "AAPL")
        short_aapl = fund_signals.build_fund_accumulation_signal("short", session, "AAPL")
        long_msft = fund_signals.build_fund_accumulation_signal("long", session, "MSFT")
        long_amzn = fund_signals.build_fund_accumulation_signal("long", session, "AMZN")
        flat = fund_signals.build_fund_accumulation_signal("long", session, "KO")
        no_direction = fund_signals.build_fund_accumulation_signal(None, session, "AAPL")
        unheld = fund_signals.build_fund_accumulation_signal("long", session, "ZZZZ")
    assert (long_aapl.would_score, short_aapl.would_score) == (1, -1)  # one fund: the first point (3 funds are the strong reading)
    assert fund_signals.FUND_SCORE_CAP == 2
    assert long_aapl.available and long_aapl.value == "1 buying, 0 selling" and "45 days" in long_aapl.reason
    assert long_msft.would_score == -1  # trimmed: net selling argues against a long
    assert long_amzn.would_score == -1  # sold out
    assert flat.would_score == 0 and flat.value == "0 buying, 0 selling"
    assert no_direction.would_score == 0 and no_direction.available
    assert unheld.value == "none" and unheld.would_score == 0 and unheld.available


def test_fund_accumulation_reports_unavailable_when_nothing_was_ever_loaded_and_ignores_stale_filings(session):
    empty = fund_signals.build_fund_accumulation_signal("long", session, "AAPL")
    assert empty.available is False and empty.would_score == 0
    assert fund_signals.build_fund_accumulation_signal("long", None, "AAPL").available is False
    loaded_funds(session)
    with as_of(when("2026-01-15T00:00:00")):  # more than 120 days after the August filing
        stale = fund_signals.build_fund_accumulation_signal("long", session, "AAPL")
    assert stale.available and stale.value == "none" and stale.would_score == 0
    with as_of(when("2025-05-20T00:00:00")):  # only Q1 was filed: nothing to compare, so no buying or selling
        first_only = fund_signals.build_fund_accumulation_signal("long", session, "AAPL")
    assert first_only.would_score == 0


def test_a_new_13d_counts_and_neither_a_first_amendment_nor_13g_do(session):
    ingest_ownership_filing(session, stored_filing("0000000009-26-000030", datetime(2026, 9, 10, 21, 0), "SCHEDULE 13G"), "AAPL", xml=G_XML)
    ingest_ownership_filing(session, stored_filing("0000000009-26-000031", datetime(2026, 9, 12, 21, 0), "SCHEDULE 13D/A"), "AAPL", xml=D_AMENDMENT_XML)
    with as_of(datetime(2026, 9, 25, 0, 0)):
        quiet = fund_signals.build_ownership_signal("long", session, "AAPL")
        assert quiet.available and quiet.value == "none" and quiet.would_score == 0
        assert fund_signals.build_ownership_signal("long", session, "MSFT").available is False  # nothing stored for it
    ingest_ownership_filing(session, stored_filing("0000000009-26-000032", datetime(2026, 9, 20, 21, 0), "SCHEDULE 13D"), "AAPL", xml=D_XML)
    with as_of(datetime(2026, 9, 25, 0, 0)):
        up = fund_signals.build_ownership_signal("long", session, "AAPL")
        down = fund_signals.build_ownership_signal("short", session, "AAPL")
        none = fund_signals.build_ownership_signal(None, session, "AAPL")
    assert (up.would_score, down.would_score, none.would_score) == (2, -2, 0)  # a new 13D is a strong buy
    assert "Pershing Square" in up.value and "46.7%" in up.value
    with as_of(datetime(2026, 12, 31, 0, 0)):  # a stake change counts for 90 days
        assert fund_signals.build_ownership_signal("long", session, "AAPL").value == "none"
    with as_of(datetime(2026, 9, 15, 0, 0)):  # accepted later than this moment
        assert fund_signals.build_ownership_signal("long", session, "AAPL").value == "none"


def test_the_shadow_scorers_read_the_context(session):
    loaded_funds(session)
    with as_of(when("2025-09-01T00:00:00")):
        signal = fund_signals._fund_accumulation_shadow_scorer(ShadowContext(symbol="AAPL", direction="long", session=session))
    assert signal.name == "fund_accumulation" and signal.would_score == 1


# ------------------------------------------------------------------ the watcher


def make(sec: FakeFunds, symbols=("NVDA", "AAPL"), **kwargs) -> FundWatcher:
    sec.tickers.update({"NVDA": NVDA, "AAPL": AAPL})
    for cik in (NVDA, AAPL, FUND):
        sec.submissions.setdefault(cik, submissions([]))
    return FundWatcher(client=sec.client, symbols=lambda: list(symbols), matcher=matcher(), **kwargs)


def settings(**kw) -> AppSettings:
    return AppSettings(smart_money_followed_funds=[str(FUND)], **kw)


def poll(watcher, session, now=NOW):
    return watcher.poll(WatcherContext(session=session, settings=settings(), now=now))


def test_a_new_13f_gives_one_summary_and_one_event_for_a_new_position_in_a_watchlist_symbol(session):
    sec = FakeFunds()
    serve_two_quarters(sec)
    events = poll(make(sec), session)
    kinds = {e.kind: e for e in events}
    assert sorted(kinds) == ["fund_13f_filed", "fund_new_position"]
    summary = kinds["fund_13f_filed"]
    assert summary.symbol is None and summary.severity == "notable" and summary.known_at == when("2025-08-14T21:00:00")
    assert "1 new, 1 added, 1 trimmed, 1 sold out" in summary.headline and "Q2 2025" in summary.headline
    assert "Long positions only" in summary.headline
    assert summary.details["new"] == 1 and summary.details["period"] == "2025-06-30"
    new = kinds["fund_new_position"]
    assert new.symbol == "NVDA" and "opened a new position" in new.headline and "45 days" in new.headline
    assert new.details["weight_pct"] == pytest.approx(50000 / 590000 * 100, abs=0.01)
    # Everything is stored before an event is returned, and a second poll announces nothing.
    assert len(session.exec(select(KnownFact).where(KnownFact.kind == FactKind.FUND_FILING)).all()) == 2
    assert poll(make(sec), session) == []


def test_a_new_position_in_a_symbol_off_the_watchlist_only_gets_the_summary(session):
    sec = FakeFunds()
    serve_two_quarters(sec)
    events = poll(make(sec, symbols=("AAPL",)), session)
    assert [e.kind for e in events] == ["fund_13f_filed"]


def test_an_old_13f_is_stored_but_does_not_alert(session):
    sec = FakeFunds()
    sec.serve_13f(FUND, "0000000001-25-000001", Q1_ACCEPTED, "2025-03-31", Q1_ROWS)
    assert poll(make(sec), session, datetime(2025, 5, 30, 0, 0)) == []  # 15 days after acceptance
    assert len(session.exec(select(KnownFact).where(KnownFact.kind == FactKind.FUND_HOLDING)).all()) == 4


def test_a_quarter_without_an_earlier_one_says_so_instead_of_inventing_changes(session):
    sec = FakeFunds()
    sec.serve_13f(FUND, "0000000001-25-000001", Q1_ACCEPTED, "2025-03-31", Q1_ROWS)
    (event,) = poll(make(sec), session, datetime(2025, 5, 16, 0, 0))
    assert "no earlier quarter stored to compare" in event.headline and event.symbol is None


def test_a_notice_is_stored_but_never_announced_as_holdings(session):
    sec = FakeFunds()
    sec.serve_13f(FUND, "0000000001-25-000005", "2025-08-14T12:00:00.000Z", "2025-06-30", [], form="13F-NT")
    assert poll(make(sec), session) == []


def test_ownership_filings_alert_by_kind_and_amended_13gs_stay_quiet(session):
    sec = FakeFunds()
    watcher = make(sec)
    serve_ownership(
        sec,
        AAPL,
        ("0000000009-25-000040", "2025-08-14T15:00:00.000Z", "SCHEDULE 13D", D_XML),
        ("0000000009-25-000041", "2025-08-14T16:00:00.000Z", "SCHEDULE 13G", G_XML),
        ("0000000009-25-000042", "2025-08-14T17:00:00.000Z", "SCHEDULE 13G/A", G_AMENDMENT_XML),
        ("0000000009-25-000043", "2025-08-14T18:00:00.000Z", "SCHEDULE 13D/A", D_AMENDMENT_XML),
    )
    events = poll(watcher, session)
    by_form = {e.details["form"]: e for e in events}
    assert sorted(by_form) == ["SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G"]  # the amended 13G is only stored
    assert events[0].details["form"] == "SCHEDULE 13D" and events[0].severity == "notable"  # most important first
    assert by_form["SCHEDULE 13G"].severity == "info" and by_form["SCHEDULE 13D/A"].severity == "info"
    assert all(e.kind == "ownership_5pct" and e.symbol == "AAPL" for e in events)
    assert "may seek to influence" in by_form["SCHEDULE 13D"].headline and "46.7%" in by_form["SCHEDULE 13D"].headline
    assert "passive holder" in by_form["SCHEDULE 13G"].headline
    assert len(session.exec(select(KnownFact).where(KnownFact.kind == FactKind.OWNERSHIP_FILING)).all()) == 4
    assert watcher.last_ownership_mode == "per_company"
    assert poll(watcher, session) == []


def test_an_ownership_filing_older_than_three_days_is_stored_without_an_event(session):
    sec = FakeFunds()
    serve_ownership(sec, AAPL, ("0000000009-25-000040", "2025-08-10T15:00:00.000Z", "SCHEDULE 13D", D_XML))
    assert poll(make(sec), session) == []
    assert len(session.exec(select(KnownFact).where(KnownFact.kind == FactKind.OWNERSHIP_FILING)).all()) == 1


def test_a_followed_funds_own_13d_on_a_watchlist_company_alerts(session):
    sec = FakeFunds()
    sec.tickers = {"HHH": HHH}
    serve_ownership(sec, FUND, ("0000000009-25-000050", "2025-08-14T15:00:00.000Z", "SCHEDULE 13D", D_XML))
    events = poll(make(sec, symbols=("HHH", "NVDA")), session)
    assert [(e.symbol, e.details["form"]) for e in events] == [("HHH", "SCHEDULE 13D")]
    # Off the watchlist the filing is stored for the Activists view but is not an event.
    other = FakeFunds()
    other.tickers = {"HHH": HHH}
    serve_ownership(other, FUND, ("0000000009-25-000051", "2025-08-14T15:00:00.000Z", "SCHEDULE 13D", D_XML))
    with Session(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})) as fresh:
        SQLModel.metadata.create_all(fresh.get_bind())
        assert poll(make(other, symbols=("NVDA",)), fresh) == []
        assert len(fresh.exec(select(KnownFact).where(KnownFact.kind == FactKind.OWNERSHIP_FILING)).all()) == 1


def test_a_big_watchlist_reads_the_market_wide_feed_and_asks_only_the_companies_in_it(session):
    sec = FakeFunds()
    feed = (
        '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        "<title>SCHEDULE 13D - Apple Inc (0000320193) (Subject)</title><updated>2025-08-14T15:30:00-04:00</updated>"
        "<id>urn:tag:sec.gov,2008:accession-number=0000000009-25-000040</id></entry><entry>"
        "<title>SCHEDULE 13G - Someone (0000999999) (Filed by)</title><updated>2025-08-14T15:30:00-04:00</updated>"
        "<id>urn:tag:sec.gov,2008:accession-number=0000000009-25-000099</id></entry></feed>"
    ).encode()
    sec.feeds = {"SCHEDULE%2013D": feed}
    serve_ownership(sec, AAPL, ("0000000009-25-000040", "2025-08-14T15:00:00.000Z", "SCHEDULE 13D", D_XML))
    watcher = make(sec, feed_mode_min_symbols=1)
    events = poll(watcher, session)
    assert watcher.last_ownership_mode == "feed"
    assert [e.kind for e in events] == ["ownership_5pct"]
    asked = [u for u in sec.urls if "submissions/CIK" in u]
    assert not any("CIK0001045810" in u for u in asked)  # NVDA is not in the feed: not asked about
    entries = parse_ownership_feed(feed)
    assert [(e["form"], e["cik"], e["role"]) for e in entries] == [("SCHEDULE 13D", 320193, "subject"), ("SCHEDULE 13G", 999999, "filed by")]
    assert entries[0]["updated"] == datetime(2025, 8, 14, 19, 30)  # naive UTC


def test_the_feed_parser_refuses_entities_and_skips_odd_entries():
    with pytest.raises(DataProviderError):
        parse_ownership_feed(b'<!DOCTYPE x [<!ENTITY a "b">]><feed/>')
    assert parse_ownership_feed(b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Unrelated</title></entry></feed>') == []


def test_one_failing_part_does_not_hide_what_the_others_found(session):
    sec = FakeFunds()
    serve_ownership(sec, AAPL, ("0000000009-25-000040", "2025-08-14T15:00:00.000Z", "SCHEDULE 13D", D_XML))
    sec.status[f"CIK{FUND:010d}"] = 429  # the followed fund's index is refused
    events = poll(make(sec), session)
    assert [e.kind for e in events] == ["ownership_5pct"]


def test_a_poll_that_finds_nothing_and_was_refused_reports_the_failure(session):
    sec = FakeFunds()
    watcher = make(sec)
    sec.status["submissions/CIK"] = 429
    with pytest.raises(DataProviderError):
        poll(watcher, session)


def test_the_runner_stores_the_events_and_sends_alerts_for_both_kinds(session):
    sec = FakeFunds()
    serve_two_quarters(sec)
    sent: list[str] = []
    runner_settings = settings(watchers_enabled=True, watchers_action="alert", telegram_bot_token="t", telegram_chat_id="c")
    result = run_watcher_once(session, make(sec), runner_settings, NOW, notifier=lambda *a: sent.append(a[2]))
    assert result.error is None and result.new_events == 2 and result.fired == 2
    assert any("13F" in text for text in sent) and any("NVDA" in text for text in sent)


def test_the_watcher_is_registered_once_and_not_at_import():
    registry.unregister_watcher(fund_watcher.WATCHER_NAME)
    try:
        assert registry.get_watcher("fund_filings") is None
        register_fund_watcher()
        register_fund_watcher()  # idempotent
        watcher = registry.get_watcher("fund_filings")
        assert watcher.poll_interval_seconds == 6 * 3600 and watcher.cooldown_seconds == 0 and watcher.daily_fire_cap > 0
    finally:
        registry.unregister_watcher(fund_watcher.WATCHER_NAME)


# ------------------------------------------------------------------ the service and endpoints


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(engine):
    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_app_settings] = lambda: settings()
    yield TestClient(app)
    app.dependency_overrides.clear()


def seed(engine) -> None:
    with Session(engine) as s:
        loaded_funds(s)
        ingest_ownership_filing(s, stored_filing("0000000009-26-000030", datetime(2026, 9, 10, 21, 0), "SCHEDULE 13G"), "AAPL", xml=G_XML)
        ingest_ownership_filing(s, stored_filing("0000000009-26-000032", datetime(2026, 9, 20, 21, 0), "SCHEDULE 13D"), "HHH", xml=D_XML)


def test_the_funds_overview_lists_followed_funds_with_their_latest_quarter(client, engine):
    before = client.get("/api/smart-money/funds").json()
    assert before["has_data"] is False and [f["cik"] for f in before["funds"]] == [str(FUND)]
    seed(engine)
    body = client.get("/api/smart-money/funds").json()
    (fund,) = body["funds"]
    assert body["has_data"] and body["using_starter_list"] is False
    assert (fund["latest_period"], fund["quarters_stored"], fund["holdings_count"], fund["matched_count"]) == ("2025-06-30", 2, 4, 4)
    assert fund["name"] == "Test Fund LP" and fund["value_unit"] == "dollars" and fund["latest_form"] == "13F-HR"


def test_the_overview_falls_back_to_the_starter_funds_when_none_are_set(client):
    app.dependency_overrides[get_app_settings] = lambda: AppSettings()
    body = client.get("/api/smart-money/funds").json()
    assert body["using_starter_list"] and len(body["funds"]) == 6
    assert {f["name"] for f in body["funds"]} >= {"Berkshire Hathaway", "Scion Asset Management"}
    assert all(f["has_data"] is False and f["is_starter"] for f in body["funds"])


def test_changes_endpoint_filters_by_status_and_hides_unchanged_by_default(client, engine):
    seed(engine)
    body = client.get(f"/api/smart-money/funds/{FUND}/changes").json()
    assert body["has_data"] and body["has_comparison"] and body["counts"] == {"new": 1, "added": 1, "trimmed": 1, "sold_out": 1, "unchanged": 1}
    assert sorted(c["status"] for c in body["changes"]) == ["added", "new", "sold_out", "trimmed"]
    assert body["top_holdings"][0]["symbol"] == "AAPL" and body["top_holdings"][0]["weight_pct"] == pytest.approx(50.85, abs=0.01)
    only = client.get(f"/api/smart-money/funds/{FUND}/changes", params={"status": "unchanged"}).json()
    assert [c["symbol"] for c in only["changes"]] == ["KO"] and only["total"] == 1
    empty = client.get("/api/smart-money/funds/1336528/changes").json()
    assert empty["has_data"] is False and empty["changes"] == []


def test_holders_endpoint_is_case_insensitive_and_limited_to_followed_funds(client, engine):
    seed(engine)
    body = client.get("/api/smart-money/funds/holders/aapl").json()
    assert body["symbol"] == "AAPL" and body["funds_stored"] == 1
    (holder,) = body["holders"]
    assert holder["cik"] == str(FUND) and holder["status"] == "added" and holder["shares"] == 1500.0
    app.dependency_overrides[get_app_settings] = lambda: AppSettings(smart_money_followed_funds=["1336528"])
    assert client.get("/api/smart-money/funds/holders/AAPL").json()["holders"] == []


def test_ownership_endpoint_counts_and_filters(client, engine):
    with Session(engine) as s:  # accepted a few days before "now", whatever the real date is
        recent = utcnow_naive().replace(microsecond=0)
        ingest_ownership_filing(s, stored_filing("0000000009-26-000070", recent, "SCHEDULE 13G"), "AAPL", xml=G_XML)
        ingest_ownership_filing(s, stored_filing("0000000009-26-000071", recent, "SCHEDULE 13D", HHH), "HHH", xml=D_XML)
        ingest_ownership_filing(s, stored_filing("0000000009-24-000072", datetime(2024, 1, 1), "SCHEDULE 13G"), "AAPL", xml=G_XML)
    body = client.get("/api/smart-money/ownership").json()
    assert body["has_data"] and (body["total"], body["count_13d"], body["count_13g"]) == (2, 1, 1)  # the 2024 one is outside 90 days
    only_d = client.get("/api/smart-money/ownership", params={"schedule": "13D"}).json()
    assert [(f["schedule"], f["symbol"], f["percent"]) for f in only_d["filings"]] == [("13D", "HHH", 46.7)]
    assert only_d["count_13g"] == 1  # the counts describe the window, not the filter
    assert client.get("/api/smart-money/ownership", params={"symbol": "aapl"}).json()["total"] == 1
    assert client.get("/api/smart-money/ownership", params={"days": 365 * 5}).status_code == 422
    assert client.get("/api/smart-money/ownership", params={"days": 365}).json()["total"] == 2


def test_get_endpoints_never_write(client, engine):
    seed(engine)
    with Session(engine) as s:
        before = len(s.exec(select(KnownFact)).all())
    for path in (
        "/api/smart-money/funds",
        f"/api/smart-money/funds/{FUND}/changes",
        "/api/smart-money/funds/holders/AAPL",
        "/api/smart-money/ownership",
    ):
        assert client.get(path).status_code == 200
    with Session(engine) as s:
        assert len(s.exec(select(KnownFact)).all()) == before


def test_endpoint_validation(client):
    assert client.get("/api/smart-money/funds/abc/changes").status_code == 422
    assert client.get("/api/smart-money/funds/123/changes", params={"status": "bogus"}).status_code == 422
    assert client.get("/api/smart-money/ownership", params={"days": 0}).status_code == 422
    assert client.get("/api/smart-money/ownership", params={"days": 9999}).status_code == 422
    assert client.get("/api/smart-money/ownership", params={"schedule": "14D"}).status_code == 422


def test_refresh_loads_funds_and_company_filings_and_reports_what_it_stored(session):
    sec = FakeFunds()
    serve_two_quarters(sec)
    sec.tickers = {"AAPL": AAPL}
    serve_ownership(sec, AAPL, ("0000000009-26-000060", (utcnow_naive() - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "SCHEDULE 13G", G_XML))
    result = funds_service.refresh_funds(session, settings(), ["AAPL", "BTC-USD"], client=sec.client, matcher=matcher())
    assert (result.funds_processed, result.filings_ingested, result.holdings_created) == (1, 2, 8)
    assert result.ownership_filings_created == 1 and result.errors == []
    again = funds_service.refresh_funds(session, settings(), ["AAPL"], client=sec.client, matcher=matcher())
    assert (again.filings_ingested, again.holdings_created) == (0, 0)


def test_refresh_reports_a_failing_fund_and_carries_on(session):
    sec = FakeFunds()  # no submissions for the followed fund
    result = funds_service.refresh_funds(session, settings(), [], client=sec.client, matcher=matcher())
    assert result.funds_processed == 1 and result.filings_ingested == 0 and result.errors


def test_refresh_endpoint_has_a_cooldown(client, monkeypatch):
    calls: list[object] = []

    def fake(session, settings_, symbols=None, **kw):
        calls.append(symbols)
        return funds_service.FundsRefreshResponse(
            funds_processed=1, filings_ingested=0, holdings_created=0, ownership_filings_created=0, legacy_skipped=0, errors=[]
        )

    monkeypatch.setattr(funds_service, "refresh_funds", fake)
    assert client.post("/api/smart-money/funds/refresh").status_code == 200
    second = client.post("/api/smart-money/funds/refresh", json={"symbols": ["X"]})
    assert second.status_code == 429 and "wait" in second.json()["detail"]
    assert calls == [None]
