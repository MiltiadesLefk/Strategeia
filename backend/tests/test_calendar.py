"""Economic calendar client, macro-table cross-check, the merged calendar timeline
and its endpoint. Fixtures only: the feed is a canned list, providers are fakes."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_data_provider, get_session
from app.data_providers import econ_calendar as ec
from app.data_providers.base import AllProvidersFailedError
from app.data_providers.universe import UniverseEntry
from app.main import app
from app.portfolio.models import PaperPosition
from app.services import calendar_service as cs

NOW = datetime(2026, 10, 1, 16, 0)  # naive UTC = 12:00 Eastern, Thursday


def _row(title, when, country="USD", impact="High", forecast="", previous=""):
    return {"title": title, "country": country, "date": when, "impact": impact, "forecast": forecast, "previous": previous}


# Friday 2 Oct 2026 is the jobs report in the built-in table; the feed agrees.
FEED = [
    _row("Non-Farm Employment Change", "2026-10-02T08:30:00-04:00", forecast="150K", previous="142K"),
    _row("Unemployment Rate", "2026-10-02T08:30:00-04:00", impact="High", forecast="4.3%", previous="4.3%"),
    _row("FOMC Member Bowman Speaks", "2026-10-01T08:15:00-04:00", impact="Low"),
    _row("ECB President Lagarde Speaks", "2026-10-01T09:30:00-04:00", country="EUR", impact="Medium"),
    _row("Bank Holiday", "2026-10-03T00:00:00-04:00", impact="Holiday"),
]


@pytest.fixture(autouse=True)
def _fresh_feed():
    ec.reset_econ_calendar_cache()
    yield
    ec.reset_econ_calendar_cache()


# ------------------------------------------------------------ feed client ----

def test_parse_feed_keeps_us_only_converts_times_and_flags_all_day():
    events = ec.parse_feed(FEED + [{"title": "broken"}, {"title": "no offset", "country": "USD", "date": "2026-10-01T08:00:00"}])
    assert [e.title for e in events] == [
        "FOMC Member Bowman Speaks", "Non-Farm Employment Change", "Unemployment Rate", "Bank Holiday",
    ]
    nfp = events[1]
    assert nfp.country == "US" and nfp.date_et == date(2026, 10, 2) and nfp.time_et == "08:30"
    assert nfp.starts_at == datetime(2026, 10, 2, 12, 30)  # UTC
    assert nfp.forecast == "150K" and nfp.previous == "142K" and nfp.actual is None
    assert events[3].time_et is None and events[3].impact == "Holiday"


def test_eastern_date_can_differ_from_the_utc_date():
    late = ec.parse_feed([_row("Late Event", "2026-10-01T22:30:00-04:00")])[0]
    assert late.date_et == date(2026, 10, 1) and late.starts_at.date() == date(2026, 10, 2)


def test_get_econ_calendar_caches_and_reports_coverage():
    calls = []

    def fetch(url):
        calls.append(url)
        return FEED

    first = ec.get_econ_calendar(fetch=fetch, now=NOW)
    second = ec.get_econ_calendar(fetch=fetch, now=NOW)
    assert first.available and first is second and len(calls) == 1
    assert first.covers_from == date(2026, 10, 1) and first.covers_to == date(2026, 10, 3)


def test_failure_is_unavailable_then_backed_off_without_refetching():
    calls = []

    def boom(url):
        calls.append(url)
        raise ValueError("empty body")

    result = ec.get_econ_calendar(fetch=boom, now=NOW)
    assert not result.available and "empty body" in result.detail and result.events == []
    ec.get_econ_calendar(fetch=boom, now=NOW)
    assert len(calls) == 1  # the backoff holds


def test_failed_refresh_serves_the_earlier_copy_marked_stale(monkeypatch):
    ec.get_econ_calendar(fetch=lambda url: FEED, now=NOW)
    # let the fresh window pass, then fail the refresh
    monkeypatch.setattr(ec, "_cached_at_monotonic", ec._cached_at_monotonic - ec.FEED_TTL_SECONDS - 1)

    def boom(url):
        raise RuntimeError("503")

    result = ec.get_econ_calendar(fetch=boom, now=NOW + timedelta(hours=4))
    assert result.available and result.stale and len(result.events) == 4
    assert "503" in result.detail


# ---------------------------------------------------- macro-table cross-check ----

def _result(rows):
    events = ec.parse_feed(rows)
    return ec.EconCalendarResult(
        events=events, available=True, fetched_at=NOW,
        covers_from=min(e.date_et for e in events), covers_to=max(e.date_et for e in events),
    )


def test_series_matching_uses_the_feeds_wording():
    assert ec.macro_series_for_title("CPI m/m") == "CPI release"
    assert ec.macro_series_for_title("Core CPI y/y") == "CPI release"
    assert ec.macro_series_for_title("Federal Funds Rate") == "FOMC decision"
    assert ec.macro_series_for_title("Non-Farm Employment Change") == "jobs report"
    assert ec.macro_series_for_title("Unemployment Rate") is None


def test_cross_check_agrees_when_feed_and_table_match():
    assert ec.compare_with_macro_table(_result(FEED)) == []


def test_cross_check_flags_a_feed_date_the_table_lacks_and_a_table_date_the_feed_lacks():
    # Table: jobs report Fri 2 Oct. Feed: puts it on Thu 1 Oct and lists nothing on the 2nd.
    rows = [
        _row("Non-Farm Employment Change", "2026-10-01T08:30:00-04:00"),
        _row("Unemployment Rate", "2026-10-02T08:30:00-04:00"),
    ]
    messages = [m.message for m in ec.compare_with_macro_table(_result(rows))]
    assert any("feed lists 2026-10-01, the built-in table does not (it has 2026-10-02)" in m for m in messages)
    assert any("built-in table lists 2026-10-02, the feed does not (it has 2026-10-01)" in m for m in messages)


def test_cross_check_never_edits_the_table_and_is_empty_without_a_feed():
    before = list(ec.macro_calendar.ALL_MACRO_EVENTS)
    ec.compare_with_macro_table(_result([_row("CPI m/m", "2026-10-01T08:30:00-04:00")]))
    assert ec.macro_calendar.ALL_MACRO_EVENTS == before
    assert ec.compare_with_macro_table(ec.EconCalendarResult(available=False)) == []


def test_cross_check_ignores_days_outside_the_tables_coverage():
    # CPI table ends 2026-12-31: a 2027 CPI date in the feed is "not tracked", not a mismatch.
    rows = [_row("CPI m/m", "2027-01-13T08:30:00-05:00")]
    assert [m for m in ec.compare_with_macro_table(_result(rows)) if m.feed_date == date(2027, 1, 13)] == []


# ------------------------------------------------------- merged timeline ----

class FakeProvider:
    name = "fake"

    def __init__(self, dates: dict[str, date | None] | None = None, failing: set[str] | None = None):
        self.dates = dates or {}
        self.failing = failing or set()

    def get_earnings_date(self, symbol):
        if symbol in self.failing:
            raise AllProvidersFailedError(symbol)
        return self.dates.get(symbol)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def _universe(monkeypatch):
    monkeypatch.setattr(
        cs.universe, "load_universe",
        lambda: [UniverseEntry("AAA", "Alpha", "Technology"), UniverseEntry("BBB", "Beta", "Energy"), UniverseEntry("BTC-USD", "Bitcoin", "Crypto")],
    )


def _open(session, symbol, direction="long"):
    session.add(PaperPosition(symbol=symbol, direction=direction, entry_price=10, stop_loss=9, tp1=12, tp2=13, shares=1))
    session.commit()


def _build(session, provider=None, econ=None, **kw):
    return cs.build_calendar(session, provider or FakeProvider(), now=NOW, econ=econ if econ is not None else _result(FEED), **kw)


def test_timeline_merges_sources_sorted_with_countdowns(session):
    provider = FakeProvider({"AAA": date(2026, 10, 6), "BBB": date(2027, 3, 1)})
    r = _build(session, provider, from_date=date(2026, 10, 1), to_date=date(2026, 10, 31))
    titles = [i.title for i in r.items]
    assert "AAA earnings" in titles and "BBB earnings" not in titles  # BBB is outside the range
    assert "ECB President Lagarde Speaks" not in titles  # US only
    assert r.today == "2026-10-01"
    aaa = next(i for i in r.items if i.title == "AAA earnings")
    assert aaa.days_until == 5 and aaa.source == "provider"
    # dates are non-decreasing
    assert [i.date for i in r.items] == sorted(i.date for i in r.items)
    # table rows later in the month (CPI 14 Oct, FOMC 28 Oct) appear as built-in rows
    macro = {i.title: i for i in r.items if i.kind == "macro"}
    assert {"CPI release", "FOMC decision"} <= set(macro)
    assert macro["FOMC decision"].time_et == "14:00" and macro["FOMC decision"].starts_at == "2026-10-28T18:00:00Z"


def test_feed_row_replaces_the_table_row_for_the_same_release_and_day(session):
    r = _build(session, from_date=date(2026, 10, 2), to_date=date(2026, 10, 2))
    nfp = [i for i in r.items if "Non-Farm" in i.title or i.title == "Jobs report"]
    assert [i.kind for i in nfp] == ["economic"]
    assert nfp[0].forecast == "150K" and nfp[0].previous == "142K" and nfp[0].confirmed_by_feed is True


def test_table_row_stays_when_the_feed_lacks_it_on_a_covered_day(session):
    rows = [_row("Unemployment Rate", "2026-10-02T08:30:00-04:00"), _row("Something", "2026-10-03T10:00:00-04:00")]
    r = _build(session, econ=_result(rows), from_date=date(2026, 10, 2), to_date=date(2026, 10, 2))
    table_row = next(i for i in r.items if i.kind == "macro")
    assert table_row.title == "Jobs report" and table_row.confirmed_by_feed is False
    assert any("built-in table lists 2026-10-02" in m.message for m in r.mismatches)


def test_feed_down_still_shows_built_in_dates_and_says_so(session):
    down = ec.EconCalendarResult(available=False, detail="empty body")
    r = _build(session, econ=down, from_date=date(2026, 10, 1), to_date=date(2026, 10, 31))
    assert {i.kind for i in r.items} == {"macro"}
    status = {s.key: s for s in r.sources}
    assert status["economic"].status == "unavailable"
    assert "showing the built-in Fed/CPI/jobs dates only" in status["economic"].detail
    assert r.mismatches == []


def test_positions_flag_and_per_position_catalysts(session):
    _open(session, "AAA")
    provider = FakeProvider({"AAA": date(2026, 10, 6)})
    r = _build(session, provider, from_date=date(2026, 10, 1), to_date=date(2026, 10, 31))
    by_title = {i.title: i for i in r.items}
    assert by_title["AAA earnings"].my_position and by_title["AAA earnings"].position_symbols == ["AAA"]
    # a market-wide release inside the next two weeks matters to every open position
    assert by_title["Non-Farm Employment Change"].my_position
    assert by_title["CPI release"].my_position  # 14 Oct is 13 days out
    assert not by_title["FOMC decision"].my_position  # 28 Oct is past the horizon
    assert not by_title["FOMC Member Bowman Speaks"].my_position  # low impact
    group = r.position_catalysts[0]
    assert group.symbol == "AAA" and group.direction == "long"
    assert [(c.kind, c.date) for c in group.catalysts][:2] == [("macro", "2026-10-02"), ("earnings", "2026-10-06")]
    assert all(c.days_until <= cs.POSITION_HORIZON_DAYS for c in group.catalysts)


def test_no_positions_means_nothing_is_flagged(session):
    r = _build(session, from_date=date(2026, 10, 1), to_date=date(2026, 10, 31))
    assert not any(i.my_position for i in r.items) and r.position_catalysts == []


def test_held_symbols_outside_the_watchlist_and_explicit_symbols_get_earnings_rows(session):
    _open(session, "ZZZ")
    provider = FakeProvider({"ZZZ": date(2026, 10, 8), "QQQ": date(2026, 10, 9)})
    r = _build(session, provider, from_date=date(2026, 10, 1), to_date=date(2026, 10, 31), symbols=["QQQ"])
    assert {"ZZZ earnings", "QQQ earnings"} <= {i.title for i in r.items}
    assert r.earnings_symbols_checked == 4  # ZZZ, QQQ, AAA, BBB (crypto skipped)


def test_earnings_failures_make_the_source_partial_or_unavailable(session):
    r = _build(session, FakeProvider(failing={"AAA"}), from_date=date(2026, 10, 1), to_date=date(2026, 10, 7))
    assert next(s for s in r.sources if s.key == "earnings").status == "partial"
    r = _build(session, FakeProvider(failing={"AAA", "BBB"}), from_date=date(2026, 10, 1), to_date=date(2026, 10, 7))
    assert next(s for s in r.sources if s.key == "earnings").status == "unavailable"


def test_macro_source_reports_series_the_table_does_not_cover(session):
    r = _build(session, from_date=date(2027, 1, 1), to_date=date(2027, 1, 31), econ=ec.EconCalendarResult(available=False))
    macro = next(s for s in r.sources if s.key == "macro")
    assert macro.status == "partial" and "CPI release" in macro.detail and "jobs report" in macro.detail
    # no 2027 CPI/jobs dates exist yet, so the only built-in row is the 27 Jan Fed decision
    assert [i.title for i in r.items if i.kind == "macro"] == ["FOMC decision"]


def test_2027_fomc_dates_appear(session):
    r = _build(session, from_date=date(2027, 1, 20), to_date=date(2027, 1, 31), econ=ec.EconCalendarResult(available=False))
    assert [(i.title, i.date) for i in r.items if i.kind == "macro"] == [("FOMC decision", "2027-01-27")]


def test_range_validation(session):
    with pytest.raises(cs.CalendarRangeError):
        _build(session, from_date=date(2026, 10, 5), to_date=date(2026, 10, 1))
    with pytest.raises(cs.CalendarRangeError):
        _build(session, from_date=date(2026, 1, 1), to_date=date(2026, 12, 31))


def test_default_range_starts_today_in_eastern_time(session):
    # 02:00 UTC on 2 Oct is still 1 Oct (22:00) in New York.
    r = cs.build_calendar(session, FakeProvider(), now=datetime(2026, 10, 2, 2, 0), econ=ec.EconCalendarResult(available=False))
    assert r.today == "2026-10-01" and r.from_date == "2026-10-01" and r.to_date == "2026-10-14"


# ------------------------------------------------------------- endpoint ----

def test_endpoint_is_read_only_and_validates_the_range(session, monkeypatch):
    monkeypatch.setattr(cs, "get_econ_calendar", lambda: _result(FEED))
    app.dependency_overrides[get_data_provider] = lambda: FakeProvider({"AAA": date(2026, 10, 6)})
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        before = len(session.exec(select(PaperPosition)).all()), len(session.exec(select(PaperPosition)).all())
        ok = client.get("/api/calendar", params={"from": "2026-10-01", "to": "2026-10-10", "symbols": "aaa"})
        bad = client.get("/api/calendar", params={"from": "2026-10-10", "to": "2026-10-01"})
        after = len(session.exec(select(PaperPosition)).all()), len(session.exec(select(PaperPosition)).all())
    finally:
        app.dependency_overrides.clear()
    assert ok.status_code == 200
    body = ok.json()
    assert body["from_date"] == "2026-10-01" and any(i["title"] == "AAA earnings" for i in body["items"])
    assert {s["key"] for s in body["sources"]} == {"economic", "macro", "earnings"}
    assert bad.status_code == 422
    assert before == after  # no position row was added or removed
