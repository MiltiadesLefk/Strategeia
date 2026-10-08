"""Congress trades beyond parsing: the as-of readers (a report filed after the
cutoff does not exist yet), clusters, the follow settings, the watcher, the silent
signal and the read-only endpoints. Real public reports saved under
tests/fixtures/house are ingested through a fake client; no network."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis.congress_scoring import build_congress_signal, score_congress_net
from app.api.deps import get_app_settings, get_session
from app.config import AppSettings
from app.data_providers.base import DataProviderError
from app.data_providers.house_disclosures import ingest_ptr
from app.knowledge import FactKind, KnownFact, as_of
from app.knowledge.congress_trades import (
    congress_clusters_as_of,
    congress_filings_as_of,
    congress_member_summaries_as_of,
    congress_trades_as_of,
    has_congress_data,
    known_members_as_of,
    member_key,
    net_buyers_as_of,
)
from app.main import app
from app.schemas.settings_schemas import SettingsUpdateRequest
from app.services import congress_service as service
from app.watchers.base import WatcherContext
from app.watchers.house_watcher import HouseWatcher
from tests.test_house_disclosures import (
    GREENE,
    KEAN,
    PELOSI,
    ROSE,
    SCANNED,
    FakeHouse,
    filing_of,
    pdf_bytes,
)

AFTER_ALL = datetime(2025, 8, 1)


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def loaded(session):
    """Pelosi (filed 17 Jan), Greene (27 Jan), Kean (19 Feb), Rose (4 Jul) and one scanned report."""
    for doc in (PELOSI, GREENE, KEAN, ROSE, SCANNED):
        ingest_ptr(session, filing_of(doc), pdf_bytes(doc))
    return session


@pytest.fixture
def client(engine):
    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_app_settings] = lambda: AppSettings()
    yield TestClient(app)
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# as-of readers
# --------------------------------------------------------------------------


def test_a_trade_whose_report_is_filed_after_the_cutoff_is_invisible(loaded):
    # Greene traded on 8 Jan but filed on 27 Jan: on 20 Jan she does not exist yet.
    assert congress_trades_as_of(loaded, "AMD", datetime(2025, 1, 20), 90) == []
    late = congress_trades_as_of(loaded, "AMD", datetime(2025, 1, 29), 90)
    assert late and {t.member for t in late} == {"Marjorie Taylor Greene"}
    assert all(t.trade_date < t.filed_date for t in late)
    assert not has_congress_data(loaded, datetime(2025, 1, 10))
    assert has_congress_data(loaded, datetime(2025, 1, 19))


def test_the_ambient_as_of_scope_is_the_default_cutoff(loaded):
    with as_of(datetime(2025, 1, 20)):
        assert {t.member for t in congress_trades_as_of(loaded, "NVDA")} == {"Nancy Pelosi"}


def test_window_counts_filing_dates_not_trade_dates(loaded):
    # Pelosi's NVDA purchase was traded 20 Dec 2024 and filed 17 Jan 2025: a 10-day
    # window on 20 Jan sees it, a 1-day window does not.
    assert congress_trades_as_of(loaded, "NVDA", datetime(2025, 1, 20), 10, side="buy")
    assert congress_trades_as_of(loaded, "NVDA", datetime(2025, 1, 20), 1, side="buy") == []


def test_members_filter_ignores_case_and_punctuation(loaded):
    rows = congress_trades_as_of(loaded, None, AFTER_ALL, 365, members=["thomas h. kean, jr"])
    assert rows and {t.member for t in rows} == {"Thomas H. Kean Jr"}
    assert congress_trades_as_of(loaded, None, AFTER_ALL, 365, members=[]) == []
    assert member_key("Thomas H. Kean Jr") == "thomas h kean jr"


def test_clusters_need_two_members_and_are_visible_from_the_second_filing(loaded):
    clusters = congress_clusters_as_of(loaded, None, AFTER_ALL, 365)
    by_symbol = {c.symbol: c for c in clusters}
    assert {"NVDA", "AMZN"} <= set(by_symbol)
    amzn = by_symbol["AMZN"]
    assert set(amzn.members) == {"Marjorie Taylor Greene", "Thomas H. Kean Jr"}
    # Kean filed on 19 Feb, so a trader could not have seen the cluster before then.
    assert amzn.visible_from.date() >= datetime(2025, 2, 19).date()
    assert amzn.total_high is not None and amzn.total_low > 0
    # Before Kean's report was filed there is no AMZN cluster at all.
    assert congress_clusters_as_of(loaded, "AMZN", datetime(2025, 2, 1), 365) == []
    # Option purchases never count: GOOGL has only option buys on the buy side.
    assert "GOOGL" not in by_symbol


def test_a_followed_subset_can_remove_a_cluster(loaded):
    assert congress_clusters_as_of(loaded, "NVDA", AFTER_ALL, 365, members=["Marjorie Taylor Greene"]) == []


def test_net_buyers_counts_distinct_members_and_stock_only(loaded):
    nvda = net_buyers_as_of(loaded, "NVDA", datetime(2025, 2, 1), 45)
    assert (nvda.buyers, nvda.sellers) == (2, 1)  # Greene and Pelosi bought; Pelosi also sold
    msft = net_buyers_as_of(loaded, "msft", AFTER_ALL, 365)
    assert (msft.buyers, msft.sellers) == (1, 1)


def test_member_summaries_and_names(loaded):
    summaries = {s.member: s for s in congress_member_summaries_as_of(loaded, AFTER_ALL, 365)}
    assert summaries["Thomas H. Kean Jr"].trade_count == 3
    assert summaries["Thomas H. Kean Jr"].state_district == "NJ07"
    assert summaries["Thomas H. Kean Jr"].median_filing_delay_days is not None
    names = [n for n, _ in known_members_as_of(loaded)]
    assert "Nancy Pelosi" in names and names == sorted(names, key=str.lower)


def test_unreadable_reports_are_listed_as_problems(loaded):
    problems = congress_filings_as_of(loaded, AFTER_ALL, only_problems=True)
    assert [p.doc_id for p in problems] == [SCANNED] and problems[0].status == "unreadable"


# --------------------------------------------------------------------------
# follow settings
# --------------------------------------------------------------------------


def test_follow_defaults_to_everyone():
    settings = AppSettings()
    assert settings.smart_money_follow_congress == "all"
    assert settings.smart_money_followed_members == [] and settings.smart_money_followed_funds == []
    assert service.followed_members(settings) is None


def test_list_mode_with_an_empty_list_follows_nobody():
    assert service.followed_members(AppSettings(smart_money_follow_congress="list")) == []
    named = AppSettings(smart_money_follow_congress="list", smart_money_followed_members=["Nancy Pelosi"])
    assert service.followed_members(named) == ["Nancy Pelosi"]


def test_follow_update_cleans_names_and_rejects_bad_values():
    req = SettingsUpdateRequest(smart_money_followed_members=["  Nancy   Pelosi ", "nancy pelosi", "Rose"])
    assert req.smart_money_followed_members == ["Nancy Pelosi", "Rose"]
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(smart_money_followed_members=["x" * 81])
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(smart_money_followed_members=["   "])
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(smart_money_followed_members=[f"Member {i}" for i in range(51)])
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(smart_money_follow_congress="some")


# --------------------------------------------------------------------------
# watcher
# --------------------------------------------------------------------------


def poll(session, settings, now, symbols=("NVDA", "AMD", "MSFT")):
    watcher = HouseWatcher(client=FakeHouse(), symbols=lambda: list(symbols))
    return watcher.poll(WatcherContext(session=session, settings=settings, now=now))


def test_a_fresh_purchase_by_a_followed_member_alerts_and_older_reports_store_quietly(session):
    events = poll(session, AppSettings(), datetime(2025, 1, 28, 12, 0))
    kinds = {(e.symbol, e.kind) for e in events}
    assert ("NVDA", "congress_buy") in kinds and ("AMD", "congress_buy") in kinds
    # Greene's report is a day old; Pelosi's (11 days) is stored but silent.
    assert all("Greene" in e.headline for e in events if e.kind == "congress_buy")
    assert ("NVDA", "congress_cluster") in kinds  # her NVDA purchase made it two members
    # Both reports are stored whatever alerted.
    assert len(session.exec(select(KnownFact).where(KnownFact.kind == FactKind.CONGRESS_TRADE)).all()) > 50
    buy = next(e for e in events if e.symbol == "AMD")
    assert "$1,001 - $15,000" in buy.headline and "filed 2025-01-27" in buy.headline


def test_only_watchlist_symbols_alert(session):
    assert poll(session, AppSettings(), datetime(2025, 1, 28, 12, 0), symbols=("MSFT",)) == []


def test_a_second_poll_finds_nothing_new(session):
    now = datetime(2025, 1, 28, 12, 0)
    poll(session, AppSettings(), now)
    assert poll(session, AppSettings(), now) == []


def test_follow_list_limits_alerts(session):
    only_pelosi = AppSettings(smart_money_follow_congress="list", smart_money_followed_members=["Nancy Pelosi"])
    assert poll(session, only_pelosi, datetime(2025, 1, 28, 12, 0)) == []  # her report is stale news


def test_following_one_member_removes_the_cluster_alert(session):
    only_greene = AppSettings(smart_money_follow_congress="list", smart_money_followed_members=["Marjorie Taylor Greene"])
    events = poll(session, only_greene, datetime(2025, 1, 28, 12, 0))
    assert events and {e.kind for e in events} == {"congress_buy"}


def test_following_nobody_alerts_nothing_but_still_stores(session):
    nobody = AppSettings(smart_money_follow_congress="list", smart_money_followed_members=[])
    assert poll(session, nobody, datetime(2025, 1, 28, 12, 0)) == []
    assert session.exec(select(KnownFact).where(KnownFact.kind == FactKind.CONGRESS_TRADE)).first() is not None


def test_a_dead_index_raises_so_the_runner_can_back_off(session):
    watcher = HouseWatcher(client=FakeHouse(index_ok=False), symbols=lambda: ["NVDA"])
    with pytest.raises(DataProviderError):
        watcher.poll(WatcherContext(session=session, settings=AppSettings(), now=datetime(2025, 1, 28)))


# --------------------------------------------------------------------------
# silent signal
# --------------------------------------------------------------------------


def test_score_is_signed_by_direction_and_capped():
    assert score_congress_net("long", 3, 0)[0] == 1
    assert score_congress_net("short", 3, 0)[0] == -1
    assert score_congress_net("long", 0, 4)[0] == -2  # a net of 4 members is the strong reading
    assert score_congress_net("long", 0, 3)[0] == -1
    assert score_congress_net("short", 5, 0)[0] == -2
    assert score_congress_net("short", 0, 4)[0] == 2
    assert score_congress_net("long", 1, 0)[0] == 0  # one member is noise
    assert score_congress_net(None, 5, 0)[0] == 0


def test_signal_is_unavailable_without_data_and_scores_with_it(session):
    assert build_congress_signal("long", None, "NVDA").available is False
    assert build_congress_signal("long", session, "NVDA").available is False
    for doc in (PELOSI, GREENE):
        ingest_ptr(session, filing_of(doc), pdf_bytes(doc))
    with as_of(datetime(2025, 2, 1)):
        sig = build_congress_signal("long", session, "NVDA")
        quiet = build_congress_signal("long", session, "ZZZZ")
    # Pelosi bought and sold NVDA stock, Greene bought: net +1 is below the bar.
    assert sig.available and sig.would_score == 0 and sig.value == "+1"
    assert quiet.available and quiet.would_score == 0


# --------------------------------------------------------------------------
# endpoints (the readers use the real clock, so ask for a year back from "now"
# by pinning the as-of moment the service reads)
# --------------------------------------------------------------------------


@pytest.fixture
def pinned(monkeypatch):
    with as_of(AFTER_ALL):
        yield


def test_status_on_an_empty_database_says_no_data(client):
    body = client.get("/api/smart-money/congress/status").json()
    assert body["has_data"] is False and body["trade_rows"] == 0
    assert "Senate" in body["senate_note"]


def test_trades_endpoint_shows_ranges_and_filing_delay(client, loaded, pinned):
    body = client.get("/api/smart-money/congress/trades", params={"days": 365, "symbol": "nvda"}).json()
    assert body["total"] > 0 and {t["symbol"] for t in body["trades"]} == {"NVDA"}
    row = next(t for t in body["trades"] if t["owner"] == "spouse" and t["side"] == "buy" and t["asset_type"] == "ST")
    assert (row["amount_low"], row["amount_high"]) == (500_001, 1_000_000)
    assert row["range_midpoint"] == 750_000.5
    assert row["filing_delay_days"] == 28 and row["state_district"] == "CA11"


def test_trade_filters_and_follow_list(client, loaded, pinned):
    sells = client.get("/api/smart-money/congress/trades", params={"days": 365, "side": "sells"}).json()
    assert sells["total"] > 0 and all(t["side"] == "sell" for t in sells["trades"])
    app.dependency_overrides[get_app_settings] = lambda: AppSettings(
        smart_money_follow_congress="list", smart_money_followed_members=["Thomas H. Kean Jr"]
    )
    body = client.get("/api/smart-money/congress/trades", params={"days": 365, "followed_only": True}).json()
    assert body["followed_only"] is True and {t["member"] for t in body["trades"]} == {"Thomas H. Kean Jr"}


def test_member_name_search_marks_followed(client, loaded):
    app.dependency_overrides[get_app_settings] = lambda: AppSettings(
        smart_money_follow_congress="list", smart_money_followed_members=["Nancy Pelosi"]
    )
    body = client.get("/api/smart-money/congress/member-names", params={"q": "pel"}).json()
    assert [(n["name"], n["followed"]) for n in body["names"]] == [("Nancy Pelosi", True)]


def test_status_counts_unreadable_reports(client, loaded):
    body = client.get("/api/smart-money/congress/status").json()
    assert body["has_data"] and body["unreadable_filings"] == 1
    assert body["problem_filings"][0]["doc_id"] == SCANNED


def test_reads_never_write(client, loaded, session, pinned):
    before = len(session.exec(select(KnownFact)).all())
    for path in ("status", "trades", "clusters", "members", "member-names"):
        assert client.get(f"/api/smart-money/congress/{path}").status_code == 200
    assert len(session.exec(select(KnownFact)).all()) == before


def test_refresh_has_a_cooldown_and_reports_a_dead_site(client, monkeypatch):
    def boom(session, **_):
        raise DataProviderError("down")

    monkeypatch.setattr(service, "refresh_congress", boom)
    assert client.post("/api/smart-money/congress/refresh").status_code == 502
    assert client.post("/api/smart-money/congress/refresh").status_code == 429
