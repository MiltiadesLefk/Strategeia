"""The smart-money tracker (NEW_PLAN phase 1): facts -> score -> candidate, both scopes,
symbol-mapping failures, look-ahead, the re-flag guard and the read-only endpoint.
Captured fixtures and synthetic facts only; nothing here touches the network."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_app_settings, get_session
from app.config import AppSettings
from app.data_providers import sec_form4
from app.knowledge import FactKind, KnownFact, as_of, make_dedupe_key, record_fact
from app.main import app
from app.smartmoney.models import STATUS_EXPIRED, STATUS_NEW, SmartMoneyCandidate
from app.smartmoney.tracker import (
    expire_stale_candidates,
    flag_candidate,
    smart_money_score_as_of,
)
from app.watchers import smart_money_watcher as smw
from app.watchers.base import WatcherContext
from app.watchers.models import WatcherState
from app.watchers.runner import run_watcher_once
from app.watchers.smart_money_watcher import SmartMoneyWatcher
from tests.test_sec_8k import FakeSec
from tests.test_sec_watcher import FEEDS, serve_form4

UNH, AAPL = 731766, 320193
# The three UnitedHealth purchases were accepted on 2025-05-16.
UNH_NOW = datetime(2025, 5, 17, 12, 0)
NOW = datetime(2026, 9, 29, 15, 0)


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def _no_house_no_funds_no_stale_maps(monkeypatch):
    """The watcher also reads the House Clerk and the followed funds; these tests are about SEC."""
    from app.data_providers.sec_13dg import reset_cik_ticker_cache

    monkeypatch.setattr(sec_form4, "_ticker_map", None)
    reset_cik_ticker_cache()
    monkeypatch.setattr(smw, "list_ptr_filings", lambda *a, **k: [])
    monkeypatch.setattr(smw, "followed_fund_ciks", lambda configured: [])


def add_buy(
    session, symbol, accession, owner, *, shares=1000.0, price=100.0, accepted=datetime(2026, 9, 22, 21, 0),
    title=None, director=False, ten_percent=False, plan=False, owners=None, code="P",
):
    record_fact(
        session,
        kind=FactKind.INSIDER_TRADE,
        symbol=symbol,
        source="test",
        source_ref=f"https://example.test/{accession}",
        dedupe_key=make_dedupe_key("form4", accession, 0),
        known_at=accepted,
        effective_at=accepted - timedelta(days=2),
        payload={
            "accession": accession, "row_index": 0, "form": "4", "filing_date": accepted.date().isoformat(),
            "period_of_report": (accepted - timedelta(days=2)).date().isoformat(), "is_amendment": False,
            "issuer_cik": 1, "symbol": symbol, "table": "non_derivative",
            "transaction_date": (accepted - timedelta(days=2)).date().isoformat(), "code": code,
            "acquired_disposed": "A", "shares": shares, "price": price, "value": shares * price,
            "owner_name": owner, "owner_key": owner.lower(), "is_officer": title is not None, "is_director": director,
            "is_ten_percent_owner": ten_percent, "is_other": False, "officer_title": title, "is_10b5_1": plan,
            "owners": owners or [],
        },
    )


def add_13d(session, symbol, accession, accepted):
    record_fact(
        session, kind=FactKind.OWNERSHIP_FILING, symbol=symbol, source="test", source_ref=f"https://example.test/{accession}",
        dedupe_key=make_dedupe_key("own", accession), known_at=accepted, known_at_basis="source",
        payload={"accession": accession, "form": "SCHEDULE 13D", "schedule": "13D", "is_amendment": False,
                 "filing_date": accepted.date().isoformat(), "filer_name": "Holder", "percent": 7.0, "shares": 1000,
                 "persons": [{"name": "Holder"}]},
    )


# --------------------------------------------------------------- the adapter


def test_only_officers_and_directors_count_and_a_joint_filing_is_one_buyer(session):
    # Two officers (the CEO is a bonus), a 10%-owner-only fund (not an insider), and a fund + its
    # director filing jointly (one family).
    add_buy(session, "AAA", "a1", "Ann CEO", title="President & CEO", shares=5000, price=100.0)  # $500k
    add_buy(session, "AAA", "a2", "Dan Director", director=True, shares=1500, price=100.0)  # $150k
    add_buy(session, "AAA", "a3", "Big Fund LP", ten_percent=True, shares=100000, price=100.0)
    add_buy(
        session, "AAA", "a4", "Big Fund LP", ten_percent=True, shares=1000, price=100.0,
        owners=[
            {"name": "Big Fund LP", "cik": "9", "is_ten_percent_owner": True, "is_officer": False, "is_director": False},
            {"name": "Eve Director", "cik": "10", "is_director": True, "is_officer": False},
        ],
    )
    with as_of(NOW):
        scored = smart_money_score_as_of(session, "AAA", NOW, AppSettings())
    buys = scored.inputs.insider_buys
    by_acc = {b.family_key: b for b in buys}
    assert len(buys) == 4
    ceo = next(b for b in buys if b.owner_key == "ann ceo")
    assert ceo.is_ceo_or_cfo and ceo.is_officer_or_director
    fund_only = next(b for b in buys if b.owner_key == "big fund lp" and b.usd == 10_000_000.0)
    assert not fund_only.is_officer_or_director  # a 10% owner alone is not an insider
    joint = next(b for b in buys if b.family_key.startswith("joint:"))
    assert joint.is_officer_or_director and joint.family_key == "joint:10|9"
    assert len(by_acc) == 4
    # Only the CEO, the director and the joint filing's director are eligible buyers.
    reasons = " ".join(c.reason for c in scored.result.components)
    assert "Ann CEO".lower() in reasons.lower() or "ann ceo" in reasons.lower()
    assert scored.result.total > 0


def test_a_fact_known_after_the_cutoff_does_not_change_the_score(session):
    add_buy(session, "AAA", "a1", "Ann CEO", title="CEO", accepted=datetime(2026, 9, 25, 14, 0))
    cutoff = datetime(2026, 9, 26, 0, 0)
    before = smart_money_score_as_of(session, "AAA", cutoff, AppSettings()).result
    add_buy(session, "AAA", "a2", "Dan Director", director=True, shares=9000, accepted=datetime(2026, 9, 27, 14, 0))
    add_13d(session, "AAA", "d1", datetime(2026, 9, 28, 10, 0))
    after_added = smart_money_score_as_of(session, "AAA", cutoff, AppSettings()).result
    assert after_added == before
    # And the same facts do count once the clock passes them.
    later = smart_money_score_as_of(session, "AAA", datetime(2026, 9, 29, 0, 0), AppSettings()).result
    assert later.total > before.total


def test_the_followed_member_multiplier_comes_from_the_setting(session):
    from app.knowledge.congress_trades import member_key  # noqa: F401

    record_fact(
        session, kind=FactKind.CONGRESS_TRADE, symbol="AAA", source="test", dedupe_key="c1",
        known_at=datetime(2026, 9, 25, 4, 0), effective_at=datetime(2026, 9, 1),
        payload={"doc_id": "d1", "row_index": 0, "member": "Nancy Pelosi", "member_key": "nancy pelosi", "owner": "self",
                 "asset_type": "ST", "ticker": "AAA", "type": "purchase", "side": "buy", "amount_low": 100001,
                 "amount_high": 250000, "amount_text": "$100,001 - $250,000", "trade_date": "2026-09-01",
                 "filed_date": "2026-09-25"},
    )
    plain = smart_money_score_as_of(session, "AAA", NOW, AppSettings()).result.total
    followed = smart_money_score_as_of(
        session, "AAA", NOW,
        AppSettings(smart_money_follow_congress="list", smart_money_followed_members=["Nancy Pelosi"],
                    smart_money_followed_member_multiplier=2.0),
    ).result.total
    assert followed == pytest.approx(plain * 2.0)


# --------------------------------------------------------------- candidates


def test_a_candidate_is_created_at_the_bar_with_its_components_and_a_reflag_guard(session):
    settings = AppSettings()
    add_buy(session, "AAA", "a1", "Ann CEO", title="CEO", shares=6000, price=100.0, accepted=datetime(2026, 9, 25, 14, 0))
    first = flag_candidate(session, smart_money_score_as_of(session, "AAA", NOW, settings), settings, scope="universe")
    assert first is not None and first.status == STATUS_NEW and first.symbol == "AAA"
    assert first.score >= settings.smart_money_min_score
    assert {c["source"] for c in first.components} == {"insider"}
    assert first.first_trigger_known_at == datetime(2026, 9, 25, 14, 0)
    assert first.expires_at == NOW + timedelta(days=14)

    # Same facts a day later: the guard holds (not 30 days, score did not rise by 2).
    tomorrow = NOW + timedelta(days=1)
    assert flag_candidate(session, smart_money_score_as_of(session, "AAA", tomorrow, settings), settings) is None

    # A 13D lifts the score by 3: flagged again at once.
    add_13d(session, "AAA", "d1", datetime(2026, 9, 29, 20, 0))
    risen = flag_candidate(session, smart_money_score_as_of(session, "AAA", tomorrow, settings), settings)
    assert risen is not None and risen.score - first.score >= 2

    rows = session.exec(select(SmartMoneyCandidate)).all()
    assert len(rows) == 2


def test_below_the_bar_nothing_is_created_and_stale_candidates_expire(session):
    settings = AppSettings()
    add_buy(session, "AAA", "a1", "Dan Director", director=True, shares=10, price=10.0, accepted=datetime(2026, 9, 25, 14, 0))
    assert smart_money_score_as_of(session, "AAA", NOW, settings).result.total < settings.smart_money_min_score
    assert flag_candidate(session, smart_money_score_as_of(session, "AAA", NOW, settings), settings) is None

    session.add(SmartMoneyCandidate(symbol="OLD", score=5, components=[], created_at=NOW - timedelta(days=20),
                                    expires_at=NOW - timedelta(days=6)))
    session.commit()
    assert expire_stale_candidates(session, NOW) == 1
    assert session.exec(select(SmartMoneyCandidate)).one().status == STATUS_EXPIRED


# --------------------------------------------------------------- the watcher, both scopes


def make_watcher(sec: FakeSec, symbols) -> SmartMoneyWatcher:
    sec.tickers.setdefault("UNH", UNH)
    sec.tickers.setdefault("AAPL", AAPL)
    return SmartMoneyWatcher(client=sec.client, house_client=sec.client, symbols=lambda: list(symbols))


def context(session, now, **settings) -> WatcherContext:
    return WatcherContext(session=session, settings=AppSettings(**settings), now=now)


def test_universe_scope_reads_only_the_universe_and_flags_a_candidate(session):
    sec = FakeSec()
    serve_form4(sec, "unh_000140", "unh_000142", "unh_000146")
    watcher = make_watcher(sec, ["UNH"])
    events = watcher.poll(context(session, UNH_NOW, smart_money_scope="universe"))
    assert watcher.last_scope == "universe" and watcher.last_scored == ["UNH"]
    assert [e.kind for e in events] == ["smart_money_candidate"]
    event = events[0]
    assert event.symbol is None  # an event with a symbol would start an evaluation; there is none in phase 1
    assert event.details["symbol"] == "UNH" and "reported" in event.headline
    candidate = session.exec(select(SmartMoneyCandidate)).one()
    assert candidate.symbol == "UNH" and candidate.scope == "universe"
    # Stored FIRST as dated facts, with the SEC acceptance time.
    facts = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.INSIDER_TRADE)).all()
    assert facts and all(f.known_at <= UNH_NOW for f in facts)
    # Nothing market-wide was read and no other company was asked about.
    assert not any("action=getcurrent" in u for u in sec.urls)
    assert not any(f"CIK{AAPL:010d}" in u for u in sec.urls)


def test_a_second_poll_asks_for_nothing_new_and_makes_no_duplicate_candidate(session):
    sec = FakeSec()
    serve_form4(sec, "unh_000140", "unh_000142", "unh_000146")
    watcher = make_watcher(sec, ["UNH"])
    watcher.poll(context(session, UNH_NOW))
    documents_asked = len([u for u in sec.urls if u.endswith(".xml")])
    again = watcher.poll(context(session, UNH_NOW + timedelta(hours=3)))
    assert again == []
    assert len([u for u in sec.urls if u.endswith(".xml")]) == documents_asked
    assert len(session.exec(select(SmartMoneyCandidate)).all()) == 1


def test_market_wide_scope_reads_the_feeds_without_a_watchlist_and_skips_unmapped_ciks(session):
    from tests.test_sec_8k import submissions

    sec = FakeSec()
    sec.feeds = {"4": (FEEDS / "feed_form4.xml").read_bytes(), "8-K": (FEEDS / "feed_8k.xml").read_bytes()}
    serve_form4(sec, "unh_000140", "unh_000142", "unh_000146")
    sec.submissions[AAPL] = submissions([])
    # The "Some Other Company" CIK (888001) of the 8-K feed is not in the ticker file: it cannot be mapped.
    watcher = make_watcher(sec, [])  # an EMPTY universe: everything found must come from the feeds
    events = watcher.poll(context(session, datetime(2026, 10, 1, 14, 0), smart_money_scope="market_wide"))
    assert watcher.last_scope == "market_wide"
    assert any("action=getcurrent" in u for u in sec.urls)
    unmapped = [e for e in events if e.kind == "smart_money_unmapped"]
    assert unmapped and all(e.symbol is None and e.details["cik"] for e in unmapped)
    assert not any("CIK0000888001" in u for u in sec.urls)  # skipped, never guessed
    assert {"UNH", "AAPL"} <= set(watcher.last_scored)
    # The companies the feeds named were asked about, with no watchlist; the 2025 purchases are older than
    # the per-company look-back at this poll moment, so nothing is stored or flagged here.
    assert any(f"CIK{UNH:010d}.json" in u for u in sec.urls) and any(f"CIK{AAPL:010d}.json" in u for u in sec.urls)
    assert session.exec(select(SmartMoneyCandidate)).all() == []


def test_market_wide_flags_a_symbol_that_is_not_in_the_universe(session):
    sec = FakeSec()
    sec.feeds = {"4": (FEEDS / "feed_form4.xml").read_bytes()}
    serve_form4(sec, "unh_000140", "unh_000142", "unh_000146")
    watcher = make_watcher(sec, ["AAPL"])
    # The poll moment is the day after the purchases, so they are inside the window.
    # (The feed fixture dates are 2026, so use a moment that still reads the 2025 filings through the window.)
    poll_now = UNH_NOW
    events = watcher.poll(context(session, poll_now, smart_money_scope="market_wide"))
    assert "UNH" in watcher.last_scored
    assert any(e.kind == "smart_money_candidate" and e.details["symbol"] == "UNH" for e in events)
    assert session.exec(select(SmartMoneyCandidate)).one().scope == "market_wide"


def test_the_runner_records_the_events_and_never_evaluates(session, monkeypatch):
    sec = FakeSec()
    serve_form4(sec, "unh_000140", "unh_000142", "unh_000146")
    watcher = make_watcher(sec, ["UNH"])
    calls = []

    def reevaluator(*args, **kwargs):  # pragma: no cover - must not be called
        calls.append(args)

    notified = []
    result = run_watcher_once(
        session, watcher, AppSettings(watchers_enabled=True), UNH_NOW,
        notifier=lambda *a: notified.append(a), reevaluator=reevaluator,
    )
    assert result.new_events == 1 and not calls and len(notified) == 1
    assert session.get(WatcherState, "smart_money").last_success_at is not None
    event_fact = session.exec(select(KnownFact).where(KnownFact.kind == FactKind.WATCHER_EVENT)).one()
    assert event_fact.payload["kind"] == "smart_money_candidate"


def test_the_watcher_is_registered_once():
    from app.watchers import registry

    smw.register_smart_money_watcher()
    smw.register_smart_money_watcher()
    assert registry.get_watcher("smart_money") is not None


# --------------------------------------------------------------- the endpoint


@pytest.fixture
def client(engine):
    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_app_settings] = lambda: AppSettings(smart_money_scope="universe")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_the_candidates_endpoint_lists_components_with_delay_notes_and_writes_nothing(client, session):
    assert client.get("/api/smart-money/candidates").json()["total"] == 0
    session.add(
        SmartMoneyCandidate(
            symbol="AAA", score=4.0, scored_at=NOW, created_at=NOW, first_trigger_known_at=NOW - timedelta(days=3),
            expires_at=NOW + timedelta(days=14), scope="universe",
            components=[{"source": "insider", "points": 4.0, "reason": "ann ceo: officer/director bought"}],
        )
    )
    session.commit()
    body = client.get("/api/smart-money/candidates").json()
    assert body["total"] == 1 and body["scope"] == "universe" and "REPORTED" in body["disclaimer"]
    comp = body["candidates"][0]["components"][0]
    assert comp["source"] == "insider" and "reported" in comp["delay_note"]
    assert client.get("/api/smart-money/candidates?status=expired").json()["total"] == 0
    assert len(session.exec(select(SmartMoneyCandidate)).all()) == 1


def test_market_wide_poll_is_capped_and_the_rest_carries_over_without_loss_or_repeat(session, monkeypatch):
    ciks = set(range(5000, 5400))  # 400 companies named by the feeds
    sec = FakeSec()
    watcher = make_watcher(sec, [])
    monkeypatch.setattr(SmartMoneyWatcher, "_feed_ciks", staticmethod(lambda client, since: set(ciks)))
    monkeypatch.setattr(smw, "ticker_for_cik", lambda cik, client=None: f"T{cik}")
    seen: list[int] = []

    def fake_ingest(context, client, mapped):
        seen.extend(mapped)
        return [mapped[c] for c in mapped], [], False

    monkeypatch.setattr(watcher, "_ingest_companies", fake_ingest)
    for hour in range(3):
        # later polls see an empty feed: only the carried-over queue remains
        if hour:
            monkeypatch.setattr(SmartMoneyWatcher, "_feed_ciks", staticmethod(lambda client, since: set()))
        watcher.poll(context(session, NOW + timedelta(hours=3 * hour), smart_money_scope="market_wide", smart_money_max_companies_per_poll=150))
        session.commit()
    assert len(seen) == 400 and set(seen) == ciks  # none lost, none done twice
    assert session.get(WatcherState, "smart_money").pending_ciks == []


def test_a_whole_universe_is_read_in_rounds_of_the_cap_and_a_small_one_in_one_poll(session, monkeypatch):
    big = {5000 + i: f"U{i}" for i in range(400)}
    watcher = make_watcher(FakeSec(), list(big.values()))
    monkeypatch.setattr(watcher, "_universe_ciks", lambda client: dict(big))
    seen: list[int] = []

    def fake_ingest(context, client, mapped):
        seen.extend(mapped)
        return list(mapped.values()), [], False

    monkeypatch.setattr(watcher, "_ingest_companies", fake_ingest)
    for hour in range(3):
        watcher.poll(context(session, NOW + timedelta(hours=3 * hour), smart_money_max_companies_per_poll=150, smart_money_scope="universe"))
        session.commit()
    assert len(seen) == 400 and set(seen) == set(big)  # one full round, nobody twice
    assert len(watcher.last_scored) == 400  # scoring still covers the whole universe each poll
    seen.clear()
    small = {1: "AAA", 2: "BBB"}
    monkeypatch.setattr(watcher, "_universe_ciks", lambda client: dict(small))
    watcher.poll(context(session, NOW + timedelta(days=1), smart_money_max_companies_per_poll=150, smart_money_scope="universe"))
    assert sorted(seen) == [1, 2]
