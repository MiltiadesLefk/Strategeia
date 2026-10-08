"""8-K filings as dated facts: item parsing, ingest, backfill, the as-of readers and
the silent negative-items signal. Nothing touches the network: SEC is a small
in-memory fake that serves submissions JSON."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from app.analysis import shadow_signals
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis import filing_8k_scoring
from app.analysis.shadow_signals import (
    LIVE_SIGNALS,
    SHADOW_POINTS_CAP,
    ShadowContext,
    evaluate_shadow_signals,
    registered_shadow_signals,
)
from app.data_providers import sec_form4
from app.data_providers.sec_8k import (
    ITEM_DICTIONARY,
    UNLISTED_ITEM_TITLE,
    Filing8K,
    backfill_8k,
    filings_8k_as_of,
    has_8k_data,
    ingest_8k_filing,
    item_category,
    item_title,
    list_8k_filings,
    parse_items,
)
from app.data_providers.sec_client import RateLimiter, SecClient
from app.knowledge import FactKind, KnownFact, LookAheadError, as_of
from app.timeutil import utcnow_naive


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def _fresh_ticker_map(monkeypatch):
    monkeypatch.setattr(sec_form4, "_ticker_map", None)


def submissions(rows: list[dict]) -> dict:
    """A submissions index `recent` block from row dicts (the real column layout)."""
    keys = {
        "accessionNumber": "accession",
        "filingDate": "filed",
        "reportDate": "report",
        "acceptanceDateTime": "accepted",
        "form": "form",
        "primaryDocument": "doc",
        "items": "items",
    }
    return {"filings": {"recent": {col: [r.get(key, "") for r in rows] for col, key in keys.items()}, "files": []}}


class FakeSec:
    """Serves the ticker file, submissions JSON, older pages, atom feeds and archive
    documents from memory; `status` forces an HTTP status for a URL."""

    def __init__(self):
        self.urls: list[str] = []
        self.submissions: dict[int, dict] = {}
        self.pages: dict[str, dict] = {}
        self.feeds: dict[str, bytes] = {}  # form type -> atom body (page 0 only; later pages are empty)
        self.documents: dict[str, bytes] = {}
        self.status: dict[str, int] = {}
        self.tickers: dict[str, int] = {}

        def http_get(url, headers, timeout):
            self.urls.append(url)
            for fragment, code in self.status.items():
                if fragment in url:
                    return code, {}, b""
            if url == sec_form4.TICKER_MAP_URL:
                body = {str(i): {"cik_str": c, "ticker": t, "title": t} for i, (t, c) in enumerate(self.tickers.items())}
                return 200, {}, json.dumps(body).encode()
            for cik, payload in self.submissions.items():
                if url == sec_form4.SUBMISSIONS_URL.format(cik=cik):
                    return 200, {}, json.dumps(payload).encode()
            for name, page in self.pages.items():
                if url == sec_form4.SUBMISSIONS_PAGE_URL.format(name=name):
                    return 200, {}, json.dumps(page).encode()
            if "action=getcurrent" in url:
                form = url.split("type=")[1].split("&")[0]
                start = int(url.split("start=")[1].split("&")[0])
                empty = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
                return 200, {}, (self.feeds.get(form, empty) if start == 0 else empty)
            if url in self.documents:
                return 200, {}, self.documents[url]
            return 404, {}, b""

        self.client = SecClient(
            limiter=RateLimiter(rate=1000, clock=lambda: 0.0, sleep=lambda s: None),
            http_get=http_get,
            sleep=lambda s: None,
        )


def row(accession, accepted, items, *, form="8-K", filed=None, report=None, doc="doc.htm"):
    return {
        "accession": accession,
        "accepted": accepted,
        "items": items,
        "form": form,
        "filed": filed or accepted[:10],
        "report": report if report is not None else accepted[:10],
        "doc": doc,
    }


# ---------------------------------------------------------------- item codes


@pytest.fixture(autouse=True)
def _signals_read_as_shadow(request, monkeypatch):
    """These tests are about how a signal is read and recorded. The signals were promoted to real scoring
    (analysis/live_evidence.py), so run the shadow loop as if none were live; the tests that assert the
    promotion itself opt out with the `promoted` marker."""
    if request.node.get_closest_marker("promoted") is None:
        monkeypatch.setattr(shadow_signals, "LIVE_SIGNALS", set())


def test_parse_items_keeps_order_and_drops_blanks_and_duplicates():
    assert parse_items("2.02,9.01") == ["2.02", "9.01"]
    assert parse_items(" 5.02 , ,5.02,9.01 ") == ["5.02", "9.01"]
    assert parse_items("") == [] and parse_items(None) == []


def test_item_dictionary_has_a_title_and_a_known_category_for_every_code():
    for code, (title, category) in ITEM_DICTIONARY.items():
        assert title and category in {"results", "leadership", "agreement", "restructuring", "regulatory", "other"}, code
    assert item_category("2.02") == "results" and item_category("5.02") == "leadership"
    assert item_category("1.01") == "agreement" and item_category("1.03") == "restructuring"
    assert item_category("4.02") == "regulatory"


def test_an_unlisted_item_is_kept_and_labelled_not_dropped():
    assert item_title("11.99") == UNLISTED_ITEM_TITLE and item_category("11.99") == "other"


# -------------------------------------------------------------------- listing


def test_listing_reads_items_acceptance_and_report_date_and_skips_other_forms():
    sec = FakeSec()
    sec.submissions[320193] = submissions(
        [
            row("0000320193-26-000018", "2026-07-30T20:30:28.000Z", "2.02,9.01", report="2026-07-30", doc="aapl-20260730.htm"),
            row("0000320193-26-000017", "2026-07-30T20:00:00.000Z", "", form="10-Q"),
            row("0000320193-26-000010", "2026-05-02T20:00:00.000Z", "5.02", form="8-K/A", report=""),
        ]
    )
    found = list_8k_filings(320193, client=sec.client)
    assert [f.accession for f in found] == ["0000320193-26-000018", "0000320193-26-000010"]
    first = found[0]
    assert first.items == ("2.02", "9.01") and first.accepted_at == datetime(2026, 7, 30, 20, 30, 28)
    assert first.report_date == date(2026, 7, 30)
    assert first.url == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/aapl-20260730.htm"
    assert first.index_url.endswith("/000032019326000018/0000320193-26-000018-index.htm")
    assert found[1].form == "8-K/A" and found[1].report_date is None


def test_listing_fetches_an_older_page_only_when_its_range_overlaps():
    sec = FakeSec()
    index = submissions([row("0000320193-26-000018", "2026-07-30T20:30:28.000Z", "2.02")])
    index["filings"]["files"] = [{"name": "old.json", "filingFrom": "2010-01-01", "filingTo": "2016-12-31"}]
    sec.submissions[320193] = index
    sec.pages["old.json"] = submissions([row("0000320193-15-000001", "2015-03-02T21:15:00.000Z", "1.01")])["filings"]["recent"]
    recent_only = list_8k_filings(320193, since=date(2026, 1, 1), client=sec.client)
    assert [f.accession for f in recent_only] == ["0000320193-26-000018"]
    assert not any("old.json" in u for u in sec.urls)
    everything = list_8k_filings(320193, since=date(2010, 1, 1), client=sec.client)
    assert [f.accession for f in everything][-1] == "0000320193-15-000001"


# --------------------------------------------------------------------- ingest


def a_filing(accession="0000320193-26-000018", accepted=datetime(2026, 7, 30, 20, 30, 28), items=("2.02", "9.01")):
    return Filing8K(
        cik=320193,
        accession=accession,
        form="8-K",
        filing_date=accepted.date(),
        accepted_at=accepted,
        primary_document="aapl-20260730.htm",
        items=tuple(items),
        report_date=date(2026, 7, 29),
    )


def test_ingest_stores_known_at_as_acceptance_and_effective_at_as_the_report_date(session):
    result = ingest_8k_filing(session, "aapl", a_filing())
    assert result.created
    fact = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.SEC_FILING_8K)).one()
    assert fact.symbol == "AAPL"
    assert fact.known_at == datetime(2026, 7, 30, 20, 30, 28) and fact.known_at_basis == "source"
    assert fact.effective_at == datetime(2026, 7, 29)
    p = fact.payload
    assert p["items"] == ["2.02", "9.01"]
    assert p["item_titles"] == ["Results of operations and financial condition", "Financial statements and exhibits"]
    assert p["categories"] == ["other", "results"]
    assert p["primary_document_url"].endswith("aapl-20260730.htm") and p["accession"] == "0000320193-26-000018"
    assert fact.source_ref == p["index_url"]


def test_ingest_twice_stores_one_fact(session):
    assert ingest_8k_filing(session, "AAPL", a_filing()).created
    assert not ingest_8k_filing(session, "AAPL", a_filing()).created
    assert len(session.exec(select(KnownFact)).all()) == 1


# --------------------------------------------------------------------- backfill


def test_backfill_is_resumable_and_reports_unknown_symbols_and_errors(session):
    sec = FakeSec()
    sec.tickers = {"AAPL": 320193, "MSFT": 789019}
    sec.submissions[320193] = submissions(
        [
            row("0000320193-26-000018", "2026-07-30T20:30:28.000Z", "2.02,9.01"),
            row("0000320193-26-000010", "2026-05-02T20:00:00.000Z", "5.02"),
        ]
    )
    sec.status["CIK0000789019"] = 500  # MSFT's index is down
    shown = []
    report = backfill_8k(session, ["AAPL", "MSFT", "ZZZZ"], date(2026, 1, 1), client=sec.client, progress=shown.append)
    assert (report.symbols, report.filings_seen, report.filings_ingested) == (3, 2, 2)
    assert report.unknown_symbols == ["ZZZZ"] and len(report.errors) == 1 and report.errors[0].startswith("MSFT")
    assert [p.filings_done for p in shown] == [1, 2]
    # Oldest first, so an interrupted run leaves a contiguous history.
    stored = session.exec(select(KnownFact).order_by(KnownFact.id)).all()
    assert [f.payload["accession"] for f in stored] == ["0000320193-26-000010", "0000320193-26-000018"]

    again = backfill_8k(session, ["AAPL"], date(2026, 1, 1), client=sec.client)
    assert (again.filings_ingested, again.filings_skipped_existing) == (0, 2)
    assert len(session.exec(select(KnownFact)).all()) == 2


# ----------------------------------------------------------------- the readers


def load(session, symbol="AAPL"):
    ingest_8k_filing(session, symbol, a_filing("0000320193-26-000010", datetime(2026, 5, 2, 20, 0), ("5.02",)))
    ingest_8k_filing(session, symbol, a_filing("0000320193-26-000018", datetime(2026, 7, 30, 20, 30), ("2.02", "9.01")))


def test_reader_respects_the_window_the_item_filter_and_newest_first(session):
    load(session)
    cutoff = datetime(2026, 8, 5)
    everything = filings_8k_as_of(session, "AAPL", cutoff, window_days=120)
    assert [f.accession for f in everything] == ["0000320193-26-000018", "0000320193-26-000010"]
    assert [f.accession for f in filings_8k_as_of(session, "AAPL", cutoff, window_days=30)] == ["0000320193-26-000018"]
    only_results = filings_8k_as_of(session, "AAPL", cutoff, window_days=120, items=["2.02"])
    assert [f.accession for f in only_results] == ["0000320193-26-000018"]
    assert only_results[0].categories == ("other", "results")


def test_a_filing_is_invisible_before_its_acceptance_time(session):
    load(session)
    assert filings_8k_as_of(session, "AAPL", datetime(2026, 7, 30, 20, 29), window_days=120)[0].accession == (
        "0000320193-26-000010"
    )
    assert filings_8k_as_of(session, "AAPL", datetime(2026, 5, 1), window_days=120) == []
    assert not has_8k_data(session, "AAPL", datetime(2026, 5, 1)) and has_8k_data(session, "AAPL", datetime(2026, 6, 1))


def test_inside_a_simulated_moment_the_default_cutoff_is_that_moment(session):
    load(session)
    with as_of(datetime(2026, 6, 1)):
        assert [f.accession for f in filings_8k_as_of(session, "AAPL", window_days=365)] == ["0000320193-26-000010"]
        with pytest.raises(LookAheadError):
            filings_8k_as_of(session, "AAPL", datetime(2026, 8, 1))


# ------------------------------------------------------------ the silent signal


def recent_filing(items, days_ago=1.0, accession="0000320193-26-000099"):
    accepted = (utcnow_naive() - timedelta(days=days_ago)).replace(microsecond=0)
    return a_filing(accession, accepted, items)


def read(session, direction, symbol="AAPL"):
    signals = {s.name: s for s in evaluate_shadow_signals(ShadowContext(symbol, direction, session))}
    return signals[filing_8k_scoring.SIGNAL_NAME]


def test_the_signal_is_registered_and_unavailable_without_stored_8ks(session):
    assert filing_8k_scoring.SIGNAL_NAME in registered_shadow_signals()
    signal = read(session, "long")
    assert not signal.available and signal.would_score == 0 and signal.value is None
    assert not filing_8k_scoring.build_8k_signal("long", None, "AAPL").available


def test_a_recent_negative_item_argues_against_a_long_and_mildly_supports_a_short(session):
    ingest_8k_filing(session, "AAPL", recent_filing(("4.02", "9.01")))
    long_read, short_read, flat_read = read(session, "long"), read(session, "short"), read(session, None)
    assert (long_read.would_score, long_read.value) == (-1, "4.02")
    assert short_read.would_score == 1
    assert flat_read.would_score == 0 and flat_read.available and "no clear direction" in flat_read.reason


def test_old_or_harmless_filings_score_zero_but_are_still_available(session):
    ingest_8k_filing(session, "AAPL", recent_filing(("2.02", "9.01"), days_ago=1, accession="0000320193-26-000098"))
    ingest_8k_filing(session, "AAPL", recent_filing(("1.03",), days_ago=10, accession="0000320193-26-000097"))
    signal = read(session, "long")
    assert signal.available and signal.would_score == 0 and signal.value == "none"


@pytest.mark.promoted
def test_the_signal_never_scores_more_than_one_point():
    assert filing_8k_scoring.FILING_8K_SCORE_CAP <= SHADOW_POINTS_CAP
    assert filing_8k_scoring.SIGNAL_NAME in LIVE_SIGNALS  # promoted to real scoring (live_evidence.py)
