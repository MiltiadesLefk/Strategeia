"""The SEC filings watcher: finding new filings, storing them before alerting,
which ones alert, the two discovery modes, and what happens when SEC says no.
Recorded Form 4 filings come from tests/fixtures/form4 and a saved shape of the
market-wide Atom feeds from tests/fixtures/sec_watcher. No network."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import AppSettings
from app.data_providers import sec_form4
from app.data_providers.base import DataProviderError
from app.knowledge import FactKind, KnownFact
from app.watchers import registry
from app.watchers.base import WatcherContext
from app.watchers.models import WatcherState
from app.watchers.runner import run_watcher_once
from app.watchers import sec_watcher
from app.watchers.sec_watcher import SecFilingsWatcher, parse_feed, register_sec_watcher
from tests.test_sec_8k import FakeSec, row, submissions
from tests.test_sec_form4 import FILINGS, filing_of, xml_of

FEEDS = Path(__file__).parent / "fixtures" / "sec_watcher"
UNH, AAPL = 731766, 320193
# The three UnitedHealth purchases were accepted on 2025-05-16 (16:22Z, 16:28Z, 22:11Z).
UNH_NOW = datetime(2025, 5, 17, 12, 0)
AAPL_NOW = datetime(2026, 9, 30, 12, 0)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def _fresh_ticker_map(monkeypatch):
    monkeypatch.setattr(sec_form4, "_ticker_map", None)


def form4_row(name: str) -> dict:
    cik, accession, accepted, document = FILINGS[name]
    return {
        "accession": accession,
        "accepted": accepted,
        "form": "4/A" if name == "aapl_4a" else "4",
        "filed": accepted[:10],
        "report": accepted[:10],
        "doc": "xslF345X03/" + document,
        "items": "",
    }


def serve_form4(sec: FakeSec, *names: str) -> None:
    """Make the named recorded filings listed for their company and downloadable."""
    by_cik: dict[int, list[dict]] = {}
    for name in names:
        by_cik.setdefault(FILINGS[name][0], []).append(form4_row(name))
        sec.documents[filing_of(name).url] = xml_of(name)
    for cik, rows in by_cik.items():
        existing = sec.submissions.get(cik)
        merged = rows + (_rows_of(existing) if existing else [])
        sec.submissions[cik] = submissions(merged)


def _rows_of(index: dict) -> list[dict]:
    recent = index["filings"]["recent"]
    return [
        {
            "accession": recent["accessionNumber"][i],
            "accepted": recent["acceptanceDateTime"][i],
            "form": recent["form"][i],
            "filed": recent["filingDate"][i],
            "report": recent["reportDate"][i],
            "doc": recent["primaryDocument"][i],
            "items": recent["items"][i],
        }
        for i in range(len(recent["form"]))
    ]


def add_8k(sec: FakeSec, cik: int, *rows: dict) -> None:
    existing = _rows_of(sec.submissions[cik]) if cik in sec.submissions else []
    sec.submissions[cik] = submissions(list(rows) + existing)


def make(sec: FakeSec, symbols=("UNH",), **kwargs) -> SecFilingsWatcher:
    sec.tickers.setdefault("UNH", UNH)
    sec.tickers.setdefault("AAPL", AAPL)
    return SecFilingsWatcher(client=sec.client, symbols=lambda: list(symbols), **kwargs)


def poll(watcher: SecFilingsWatcher, session: Session, now: datetime):
    return watcher.poll(WatcherContext(session=session, settings=AppSettings(), now=now))


def kinds(events) -> list[str]:
    return [e.kind for e in events]


# --------------------------------------------------------------- insider events


def test_buys_and_the_cluster_alert_and_everything_is_stored(session):
    sec = FakeSec()
    serve_form4(sec, "unh_000140", "unh_000142", "unh_000146")
    events = poll(make(sec), session, UNH_NOW)
    # Noseworthy bought $93.7k (below the bar, so no event of its own), Flynn $492k and Rex $5.0M.
    # Flynn's filing is also the one that made the group of two different insiders a cluster.
    assert kinds(events) == ["insider_cluster", "insider_buy", "insider_buy"]
    cluster, flynn, rex = events
    assert cluster.severity == "urgent" and cluster.details["insider_count"] == 3
    assert cluster.details["completed_by"] == "0000731766-25-000142"
    assert flynn.details["accession"] == "0000731766-25-000142" and flynn.details["net_value"] == pytest.approx(1533 * 320.80, abs=0.01)
    assert rex.details["accession"] == "0000731766-25-000146"
    assert {e.symbol for e in events} == {"UNH"}
    # Event times and links are the filing's: acceptance time, SEC filing page.
    assert flynn.known_at == datetime(2025, 5, 16, 16, 28, 22)
    assert flynn.source_ref == "https://www.sec.gov/Archives/edgar/data/731766/000073176625000142/0000731766-25-000142-index.htm"
    assert cluster.source_ref == flynn.source_ref + "#cluster"  # distinct, so neither reads as a repeat of the other
    rows = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.INSIDER_TRADE)).all()
    assert len(rows) == 3 and {r.known_at for r in rows} == {
        datetime(2025, 5, 16, 16, 22, 34),
        datetime(2025, 5, 16, 16, 28, 22),
        datetime(2025, 5, 16, 22, 11, 47),
    }


def test_a_second_poll_finds_nothing_new_and_downloads_nothing(session):
    sec = FakeSec()
    serve_form4(sec, "unh_000140", "unh_000142", "unh_000146")
    watcher = make(sec)
    poll(watcher, session, UNH_NOW)
    documents_after_first = sum(1 for u in sec.urls if "/Archives/" in u)
    assert poll(watcher, session, UNH_NOW) == []
    assert sum(1 for u in sec.urls if "/Archives/" in u) == documents_after_first
    assert len(session.exec(select(KnownFact).where(KnownFact.kind == FactKind.INSIDER_TRADE)).all()) == 3


def test_only_the_new_filing_is_found_on_a_later_poll(session):
    sec = FakeSec()
    serve_form4(sec, "unh_000140")
    watcher = make(sec)
    assert poll(watcher, session, UNH_NOW) == []  # $93.7k: stored, below the bar
    serve_form4(sec, "unh_000140", "unh_000146")
    events = poll(watcher, session, UNH_NOW)
    assert kinds(events) == ["insider_cluster", "insider_buy"]  # Rex's filing makes two insiders: a cluster
    assert events[1].details["accession"] == "0000731766-25-000146"


def test_sales_are_stored_but_never_alert(session):
    sec = FakeSec()
    serve_form4(sec, "aapl_orig")  # two open-market sales under a 10b5-1 plan
    assert poll(make(sec, ("AAPL",)), session, datetime(2022, 8, 20, 12, 0)) == []
    rows = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.INSIDER_TRADE)).all()
    assert len(rows) == 2 and {r.payload["code"] for r in rows} == {"S"}


def test_a_small_buy_alone_stays_quiet(session):
    sec = FakeSec()
    serve_form4(sec, "unh_000140")
    assert poll(make(sec), session, UNH_NOW) == []
    assert len(session.exec(select(KnownFact).where(KnownFact.kind == FactKind.INSIDER_TRADE)).all()) == 1


def test_an_old_filing_is_stored_but_does_not_alert(session):
    sec = FakeSec()
    serve_form4(sec, "unh_000146")
    assert poll(make(sec), session, datetime(2025, 5, 20, 12, 0)) == []  # three days after acceptance
    assert len(session.exec(select(KnownFact).where(KnownFact.kind == FactKind.INSIDER_TRADE)).all()) == 1


def test_grants_and_option_exercises_never_alert(session):
    sec = FakeSec()
    serve_form4(sec, "aapl_013191", "aapl_038028")  # exercises (M) and withholding (F), then grants (A)
    assert poll(make(sec, ("AAPL",)), session, datetime(2026, 4, 4, 12, 0)) == []


# ----------------------------------------------------------------------- 8-Ks


def test_an_interesting_8k_alerts_and_an_ordinary_one_is_only_stored(session):
    sec = FakeSec()
    add_8k(
        sec,
        AAPL,
        row("0000320193-26-000100", "2026-09-29T21:00:00.000Z", "2.02,9.01", report="2026-09-29"),
        row("0000320193-26-000101", "2026-09-29T22:00:00.000Z", "8.01,9.01"),
        row("0000320193-26-000102", "2026-09-29T23:00:00.000Z", "4.02", form="8-K/A"),
    )
    events = poll(make(sec, ("AAPL",)), session, AAPL_NOW)
    assert kinds(events) == ["filing_8k"]
    event = events[0]
    assert event.severity == "notable" and event.details["interesting_items"] == ["2.02"]
    assert "2.02 Results of operations" in event.headline
    assert event.known_at == datetime(2026, 9, 29, 21, 0, 0)  # acceptance time
    assert event.source_ref.endswith("0000320193-26-000100-index.htm")
    stored = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.SEC_FILING_8K)).all()
    assert len(stored) == 3  # the amendment is stored too, it just does not alert


def test_bankruptcy_delisting_and_restatement_items_are_urgent(session):
    sec = FakeSec()
    add_8k(sec, AAPL, row("0000320193-26-000100", "2026-09-29T21:00:00.000Z", "1.03,9.01"))
    (event,) = poll(make(sec, ("AAPL",)), session, AAPL_NOW)
    assert event.severity == "urgent"


def test_an_8k_is_not_alerted_twice(session):
    sec = FakeSec()
    add_8k(sec, AAPL, row("0000320193-26-000100", "2026-09-29T21:00:00.000Z", "5.02"))
    watcher = make(sec, ("AAPL",))
    assert len(poll(watcher, session, AAPL_NOW)) == 1
    assert poll(watcher, session, AAPL_NOW) == []


# ------------------------------------------------------------ order and runner


def runner_settings(**overrides) -> AppSettings:
    return AppSettings(watchers_enabled=True, watchers_action="alert", telegram_bot_token="t", telegram_chat_id="c", **overrides)


def test_facts_are_stored_before_any_alert_goes_out(session):
    sec = FakeSec()
    serve_form4(sec, "unh_000142")
    seen_when_alerted: list[int] = []

    def notifier(token, chat, text):
        seen_when_alerted.append(len(session.exec(select(KnownFact).where(KnownFact.kind == FactKind.INSIDER_TRADE)).all()))

    result = run_watcher_once(session, make(sec), runner_settings(), UNH_NOW, notifier=notifier)
    assert result.error is None and result.fired == 1 and seen_when_alerted == [1]


def test_the_runner_records_every_event_but_the_cooldown_lets_one_alert_per_company(session):
    sec = FakeSec()
    serve_form4(sec, "unh_000140", "unh_000142", "unh_000146")
    sent = []
    result = run_watcher_once(session, make(sec), runner_settings(), UNH_NOW, notifier=lambda *a: sent.append(a[2]))
    assert (result.new_events, result.fired, result.suppressed) == (3, 1, 2)
    assert len(sent) == 1 and "insiders bought" in sent[0]  # the cluster, the most important one, goes first
    events = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.WATCHER_EVENT)).all()
    assert len(events) == 3 and sum(1 for e in events if e.payload["suppressed_reason"]) == 2


def test_the_watcher_is_registered_once_and_not_at_import():
    registry.unregister_watcher(sec_watcher.WATCHER_NAME)
    try:
        assert registry.get_watcher("sec_filings") is None
        register_sec_watcher()
        register_sec_watcher()  # idempotent: no "already registered" error
        watcher = registry.get_watcher("sec_filings")
        assert watcher.poll_interval_seconds == 300 and watcher.cooldown_seconds == 6 * 3600 and watcher.daily_fire_cap > 0
    finally:
        registry.unregister_watcher(sec_watcher.WATCHER_NAME)


# ------------------------------------------------------------------ the two modes


def test_a_small_watchlist_asks_each_company_and_never_reads_the_feeds(session):
    sec = FakeSec()
    serve_form4(sec, "unh_000142")
    add_8k(sec, AAPL, row("0000320193-26-000100", "2026-09-29T21:00:00.000Z", "2.02"))
    watcher = make(sec, ("UNH", "AAPL"), feed_mode_min_symbols=2)
    poll(watcher, session, UNH_NOW)
    assert watcher.last_mode == "per_company"
    assert not any("getcurrent" in u for u in sec.urls)
    assert sum(1 for u in sec.urls if "submissions/CIK" in u) == 2


def test_a_big_watchlist_uses_the_feeds_and_only_asks_the_companies_that_filed(session):
    sec = FakeSec()
    sec.feeds = {"4": (FEEDS / "feed_form4.xml").read_bytes(), "8-K": (FEEDS / "feed_8k.xml").read_bytes()}
    sec.tickers = {"UNH": UNH, "AAPL": AAPL, "NOFILING": 555001}
    sec.submissions[UNH] = submissions([])
    add_8k(sec, AAPL, row("0000320193-26-000100", "2026-09-29T21:00:00.000Z", "2.02"))
    sec.submissions[555001] = submissions([])
    watcher = SecFilingsWatcher(client=sec.client, symbols=lambda: ["UNH", "AAPL", "NOFILING"], feed_mode_min_symbols=2)
    events = poll(watcher, session, datetime(2026, 10, 1, 18, 0))
    assert watcher.last_mode == "feed"
    # Each feed is read page by page until a page is empty (these have one page of entries).
    assert sum(1 for u in sec.urls if "getcurrent" in u) == 4
    asked = [u for u in sec.urls if "submissions/CIK" in u]
    assert any("CIK0000731766" in u for u in asked) and any("CIK0000320193" in u for u in asked)
    assert not any("CIK0000555001" in u for u in asked)  # nothing in the feed for it
    assert not any("CIK0000999001" in u or "CIK0000888001" in u for u in asked)  # off the watchlist
    assert kinds(events) == ["filing_8k"] and events[0].symbol == "AAPL"


def test_feed_mode_alerts_on_a_fresh_8k_from_a_watchlist_company(session):
    sec = FakeSec()
    sec.feeds = {"8-K": (FEEDS / "feed_8k.xml").read_bytes()}
    sec.tickers = {"AAPL": AAPL, "OTHER": 777001}
    add_8k(sec, AAPL, row("0000320193-26-000100", "2026-10-01T16:30:00.000Z", "2.02,9.01", report="2026-10-01"))
    sec.submissions[777001] = submissions([])
    watcher = SecFilingsWatcher(client=sec.client, symbols=lambda: ["AAPL", "OTHER"], feed_mode_min_symbols=1)
    events = poll(watcher, session, datetime(2026, 10, 1, 18, 0))
    assert watcher.last_mode == "feed" and kinds(events) == ["filing_8k"] and events[0].symbol == "AAPL"


def test_the_default_threshold_is_forty_companies():
    assert sec_watcher.FEED_MODE_MIN_SYMBOLS == 40
    assert SecFilingsWatcher().feed_mode_min_symbols == 40


def test_crypto_and_unregistered_symbols_are_skipped(session):
    sec = FakeSec()
    watcher = make(sec, ("BTC-USD", "^VIX", "NOTLISTED"))
    assert poll(watcher, session, UNH_NOW) == [] and watcher.last_mode is None


def test_feed_parsing_converts_to_utc_and_tells_issuer_from_reporting_owner():
    entries = parse_feed((FEEDS / "feed_form4.xml").read_bytes())
    assert [(e["form"], e["role"]) for e in entries][:3] == [("4", "reporting"), ("4", "issuer"), ("424B2", "filer")]
    assert entries[1]["cik"] == UNH and entries[1]["accession"] == "0001000000-26-000001"
    assert entries[1]["updated"] == datetime(2026, 10, 1, 17, 23, 7)  # 13:23:07 at -04:00


def test_a_feed_with_a_doctype_is_refused():
    with pytest.raises(DataProviderError):
        parse_feed(b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY x "y">]><feed xmlns="http://www.w3.org/2005/Atom"/>')


# ----------------------------------------------------------------------- failures


@pytest.mark.parametrize("code", [403, 429])
def test_sec_refusing_us_is_recorded_as_an_error_with_no_events_and_no_hammering(session, code):
    sec = FakeSec()
    serve_form4(sec, "unh_000142")
    sec.status["CIK0000320193"] = code  # companies are asked in CIK order: AAPL, then UNH
    sec.tickers["AAPL"] = AAPL
    sec.submissions[AAPL] = submissions([])
    sent = []
    result = run_watcher_once(
        session, make(sec, ("UNH", "AAPL")), runner_settings(), UNH_NOW, notifier=lambda *a: sent.append(a)
    )
    assert result.ran and result.error and str(code) in result.error and result.new_events == 0 and sent == []
    state = session.get(WatcherState, "sec_filings")
    assert state.consecutive_failures == 1 and str(code) in state.last_error
    assert session.exec(select(KnownFact).where(KnownFact.kind == FactKind.INSIDER_TRADE)).all() == []
    assert session.exec(select(KnownFact).where(KnownFact.kind == FactKind.WATCHER_EVENT)).all() == []
    if code == 403:
        # A 403 is not retried and the poll stops: the second company is never asked.
        assert not any("CIK0000731766" in u for u in sec.urls)


def test_one_unreadable_filing_does_not_lose_the_others(session):
    sec = FakeSec()
    serve_form4(sec, "unh_000142", "unh_000146")
    sec.documents.pop(filing_of("unh_000142").url)  # 404 for one document
    events = poll(make(sec), session, UNH_NOW)
    assert kinds(events) == ["insider_buy"] and events[0].details["accession"] == "0000731766-25-000146"
    # The unreadable one is not marked as seen, so the next poll tries it again.
    sec.documents[filing_of("unh_000142").url] = xml_of("unh_000142")
    again = poll(make(sec), session, UNH_NOW)
    assert any(e.details.get("accession") == "0000731766-25-000142" for e in again)


def test_a_late_failure_keeps_the_events_already_found(session):
    sec = FakeSec()
    serve_form4(sec, "unh_000146")
    sec.tickers["LATE"] = 900001  # companies are asked in CIK order: UNH first, then this one
    sec.status["CIK0000900001"] = 403
    result = run_watcher_once(session, make(sec, ("UNH", "LATE")), runner_settings(), UNH_NOW, notifier=lambda *a: None)
    assert result.error is None and result.new_events == 1  # the poll succeeded with what it had
    assert session.get(WatcherState, "sec_filings").consecutive_failures == 0


def test_an_all_failing_poll_raises(session):
    sec = FakeSec()
    sec.status["submissions/CIK"] = 500
    with pytest.raises(DataProviderError):
        poll(make(sec), session, UNH_NOW)


def test_feed_paging_stops_at_a_page_older_than_the_last_successful_poll(session):
    sec = FakeSec()
    sec.feeds = {"4": (FEEDS / "feed_form4.xml").read_bytes(), "8-K": (FEEDS / "feed_8k.xml").read_bytes()}
    sec.tickers = {"UNH": UNH, "AAPL": AAPL}
    sec.submissions[UNH] = submissions([])
    sec.submissions[AAPL] = submissions([])
    watcher = SecFilingsWatcher(client=sec.client, symbols=lambda: ["UNH", "AAPL"], feed_mode_min_symbols=1)
    poll(watcher, session, datetime(2026, 10, 2, 12, 0))  # the whole first page predates now - 6 hours
    assert sum(1 for u in sec.urls if "getcurrent" in u) == 2
