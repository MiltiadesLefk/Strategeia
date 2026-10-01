"""The Smart Money insider view: filters, clusters, summary parity with the
scorer's reader, read-only endpoints, the refresh cooldown and the status view.
Synthetic filings only; nothing here touches the network."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_app_settings, get_session
from app.config import AppSettings
from app.data_providers.sec_form4 import BackfillReport
from app.knowledge import FactKind, KnownFact, as_of, record_fact
from app.knowledge.insider_trades import insider_activity_as_of
from app.main import app
from app.services import smart_money_service as service

NOW = datetime(2026, 9, 29, 15, 0)


def add_trade(
    session,
    symbol,
    accession,
    owner,
    *,
    code="P",
    shares=1000.0,
    price=100.0,
    traded="2026-09-20",
    accepted=datetime(2026, 9, 22, 21, 0),
    title=None,
    director=False,
    ten_percent=False,
    plan=False,
    row=0,
    unpriced=False,
):
    record_fact(
        session,
        kind=FactKind.INSIDER_TRADE,
        symbol=symbol,
        source="sec_edgar",
        source_ref=f"https://www.sec.gov/Archives/edgar/data/1/{accession}/form4.xml",
        dedupe_key=f"form4|{accession}|{row}",
        known_at=accepted,
        effective_at=datetime.fromisoformat(traded),
        payload={
            "accession": accession,
            "row_index": row,
            "form": "4",
            "filing_date": accepted.date().isoformat(),
            "period_of_report": traded,
            "is_amendment": False,
            "issuer_cik": 1,
            "symbol": symbol,
            "table": "non_derivative",
            "transaction_date": traded,
            "code": code,
            "acquired_disposed": "A" if code == "P" else "D",
            "shares": shares,
            "price": None if unpriced else price,
            "value": None if unpriced else shares * price,
            "owner_name": owner,
            "owner_key": owner.lower(),
            "is_officer": title is not None,
            "is_director": director,
            "is_ten_percent_owner": ten_percent,
            "is_other": False,
            "officer_title": title,
            "is_10b5_1": plan,
        },
    )


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
def client(engine):
    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_app_settings] = lambda: AppSettings()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(session):
    """AAA: CEO and a director both buy (a cluster), a CFO sells under a plan.
    BBB: one lone buy and one small buy. CCC: a sale only."""
    add_trade(session, "AAA", "a1", "Ann CEO", title="Chief Executive Officer", traded="2026-09-18",
              accepted=datetime(2026, 9, 21, 20, 0), price=50.0, shares=4000)
    add_trade(session, "AAA", "a2", "Dan Director", director=True, traded="2026-09-20",
              accepted=datetime(2026, 9, 22, 20, 0), price=50.0, shares=2000)
    add_trade(session, "AAA", "a3", "Cy CFO", title="Chief Financial Officer", code="S", plan=True,
              traded="2026-09-23", accepted=datetime(2026, 9, 24, 20, 0), price=55.0, shares=1000)
    add_trade(session, "BBB", "b1", "Bo Owner", ten_percent=True, traded="2026-09-10",
              accepted=datetime(2026, 9, 11, 20, 0), price=10.0, shares=500)
    add_trade(session, "BBB", "b2", "Bo Owner", ten_percent=True, traded="2026-09-12",
              accepted=datetime(2026, 9, 14, 20, 0), price=10.0, shares=500, row=0, unpriced=True)
    add_trade(session, "CCC", "c1", "Cat Exec", title="Chief Operating Officer", code="S",
              traded="2026-09-01", accepted=datetime(2026, 9, 3, 20, 0), price=20.0, shares=100)
    # An old filing, outside a 30-day window.
    add_trade(session, "CCC", "c0", "Cat Exec", code="P", traded="2026-06-01", accepted=datetime(2026, 6, 3, 20, 0))
    return session


@pytest.fixture(autouse=True)
def _simulated_now():
    with as_of(NOW):
        yield


def test_role_tags_call_out_ceo_and_cfo_from_the_title():
    assert service.role_tags(("officer", "director"), "President & CEO") == ["CEO", "Director"]
    assert service.role_tags(("officer",), "Chief Financial Officer") == ["CFO"]
    assert service.role_tags(("officer",), "VP Sales") == ["Officer"]
    assert service.role_tags(("10% owner",), None) == ["10% owner"]
    assert service.role_tags(("other",), None) == ["Other"]
    assert service.role_tags((), None) == []


def test_all_trades_newest_filing_first_with_both_dates_and_delay(seeded):
    out = service.list_trades(seeded, days=30)
    assert [t.symbol for t in out.trades] == ["AAA", "AAA", "AAA", "BBB", "BBB", "CCC"]
    first = out.trades[0]  # the CFO sale filed 24 Sep
    assert first.side == "sell" and first.is_10b5_1 and first.role_tags == ["CFO"]
    assert first.transaction_date == "2026-09-23" and first.filed_after_days == 1
    assert first.known_at == datetime(2026, 9, 24, 20, 0)
    assert first.filing_url.startswith("https://www.sec.gov/")
    ceo = next(t for t in out.trades if t.insider == "Ann CEO")
    assert ceo.filed_after_days == 3 and ceo.role_tags == ["CEO"]


def test_side_filter_and_totals(seeded):
    buys = service.list_trades(seeded, days=30, side="buys")
    assert {t.side for t in buys.trades} == {"buy"} and buys.buy_count == 4 and buys.sell_count == 0
    sells = service.list_trades(seeded, days=30, side="sells")
    assert [t.symbol for t in sells.trades] == ["AAA", "CCC"]
    allrows = service.list_trades(seeded, days=30)
    assert allrows.buy_value == pytest.approx(4000 * 50 + 2000 * 50 + 500 * 10)  # the unpriced row adds none
    assert allrows.sell_value == pytest.approx(1000 * 55 + 100 * 20)
    with pytest.raises(ValueError):
        service.list_trades(seeded, side="nonsense")


def test_symbol_days_and_min_value_filters(seeded):
    only = service.list_trades(seeded, days=30, symbol=" aaa ")
    assert only.symbol == "AAA" and {t.symbol for t in only.trades} == {"AAA"}
    wide = service.list_trades(seeded, days=120, symbol="CCC")
    assert wide.total == 2  # the June purchase appears once the window reaches it
    assert service.list_trades(seeded, days=30, symbol="CCC").total == 1
    big = service.list_trades(seeded, days=30, min_value=100_000)
    assert {t.insider for t in big.trades} == {"Ann CEO", "Dan Director"}
    # An unpriced row has no value to compare, so any minimum hides it.
    assert all(t.value is not None for t in service.list_trades(seeded, days=30, min_value=1).trades)


def test_a_filing_not_yet_accepted_is_invisible(seeded):
    with as_of(datetime(2026, 9, 21, 21, 0)):
        out = service.list_trades(seeded, days=30)
    assert {t.insider for t in out.trades if t.symbol == "AAA"} == {"Ann CEO"}


def test_clusters_need_two_distinct_insiders(seeded):
    out = service.list_clusters(seeded, days=30)
    assert [c.symbol for c in out.clusters] == ["AAA"]
    cluster = out.clusters[0]
    assert cluster.insider_count == 2 and cluster.total_value == pytest.approx(300_000)
    assert set(cluster.insiders) == {"Ann CEO", "Dan Director"} and not cluster.any_10b5_1
    assert cluster.visible_from == datetime(2026, 9, 22, 20, 0)
    assert out.symbols_checked == 3  # AAA, BBB, CCC each had filings in the window
    assert service.list_clusters(seeded, days=30, symbol="BBB").clusters == []  # one person twice is not a cluster


def test_summary_matches_the_scorers_reader_and_says_what_it_would_score(seeded):
    summary = service.symbol_summary(seeded, "aaa", days=90)
    activity = insider_activity_as_of(seeded, "AAA", window_days=90)
    assert (summary.buy_count, summary.sell_count) == (activity.buy_count, activity.sell_count)
    assert summary.buy_value == activity.buy_value and summary.sell_value == activity.sell_value
    assert summary.net_value == activity.net_value
    # 300k of buys less 55k of sales is above the scorer's minimum net buy: +1 for a long, -1 for a short.
    assert summary.would_score_long == 1 and summary.would_score_short == -1
    assert summary.cluster_count == 1 and summary.newest_filing == datetime(2026, 9, 24, 20, 0)
    assert "net" in summary.score_reasons[0]


def test_summary_of_sells_only_never_scores(seeded):
    summary = service.symbol_summary(seeded, "CCC", days=30)
    assert summary.data_loaded and summary.sell_count == 1 and summary.would_score_long == 0
    assert summary.would_score_short == 0 and summary.score_reasons == []


def test_summary_with_nothing_stored_is_not_loaded_rather_than_quiet(session):
    summary = service.symbol_summary(session, "ZZZ")
    assert summary.data_loaded is False and summary.would_score_long == 0 and summary.newest_filing is None


def test_status_empty_then_filled(session, seeded):
    empty_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(empty_engine)
    with Session(empty_engine) as blank:
        empty = service.status(blank)
    assert empty.has_data is False and empty.trade_rows == 0 and empty.symbol_list == []
    assert "backfill_insider_trades.py" in empty.ingest_command
    full = service.status(seeded)
    assert full.has_data and full.trade_rows == 7 and full.symbols == 3
    assert full.symbol_list == ["AAA", "BBB", "CCC"]
    assert full.oldest_filing == datetime(2026, 6, 3, 20, 0) and full.newest_filing == datetime(2026, 9, 24, 20, 0)


def test_endpoints_return_the_service_output_and_never_write(client, engine, seeded):
    seeded.commit()
    before = _fact_count(engine)
    trades = client.get("/api/smart-money/insiders", params={"days": 30, "side": "buys", "symbol": "AAA"}).json()
    assert trades["total"] == 2 and trades["trades"][0]["known_at"].endswith("Z")
    clusters = client.get("/api/smart-money/insiders/clusters", params={"days": 30}).json()
    assert [c["symbol"] for c in clusters["clusters"]] == ["AAA"]
    summary = client.get("/api/smart-money/insiders/summary/AAA").json()
    assert summary["would_score_long"] == 1
    assert client.get("/api/smart-money/status").json()["trade_rows"] == 7
    assert _fact_count(engine) == before


def test_endpoint_validation(client):
    assert client.get("/api/smart-money/insiders", params={"side": "x"}).status_code == 422
    assert client.get("/api/smart-money/insiders", params={"days": 0}).status_code == 422
    assert client.get("/api/smart-money/insiders", params={"min_value": -1}).status_code == 422


def _fact_count(engine) -> int:
    with Session(engine) as s:
        return len(s.exec(select(KnownFact)).all())


def test_refresh_batches_symbols_new_ones_first_and_reports_the_rest(session, seeded, monkeypatch):
    calls = []

    def fake_backfill(sess, symbols, since, until=None, **kwargs):
        calls.append((list(symbols), (until - since).days))
        return BackfillReport(symbols=len(symbols), filings_seen=3, filings_ingested=2, facts_created=5)

    monkeypatch.setattr(service, "backfill_insider_trades", fake_backfill)
    wanted = ["AAA", "N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9"]
    out = service.refresh_insiders(session, wanted, today=NOW.date())
    assert out.symbols_requested == 10 and out.symbols_processed == service.REFRESH_MAX_SYMBOLS
    assert out.symbols_remaining == 2 and out.rows_created == 5
    # Symbols with nothing stored take the whole batch and get the long look-back.
    assert calls == [(["N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8"], service.REFRESH_NEW_SYMBOL_DAYS)]


def test_refresh_known_symbols_use_the_short_lookback_and_errors_are_passed_on(session, seeded, monkeypatch):
    calls = []

    def fake_backfill(sess, symbols, since, until=None, **kwargs):
        calls.append((list(symbols), (until - since).days))
        return BackfillReport(symbols=len(symbols), errors=["AAA: SEC unreachable"], unknown_symbols=["QQQQ"])

    monkeypatch.setattr(service, "backfill_insider_trades", fake_backfill)
    out = service.refresh_insiders(session, ["aaa", "AAA", "BBB", "QQQQ"], today=NOW.date())
    assert calls == [(["QQQQ"], 90), (["AAA", "BBB"], 14)]
    assert out.errors == ["AAA: SEC unreachable"] * 2 and out.unknown_symbols == ["QQQQ"] * 2


def test_refresh_endpoint_has_a_cooldown_and_uses_the_watchlist_by_default(client, monkeypatch):
    seen = []

    def fake_refresh(session, symbols, **kwargs):
        seen.append(symbols)
        return service.InsiderRefreshResponse(
            symbols_requested=len(symbols), symbols_processed=len(symbols), symbols_remaining=0, filings_seen=0,
            filings_ingested=0, rows_created=0, unknown_symbols=[], errors=[],
        )

    monkeypatch.setattr(service, "refresh_insiders", fake_refresh)
    monkeypatch.setattr(service, "default_refresh_symbols", lambda n: ["WL1", "WL2"])
    assert client.post("/api/smart-money/insiders/refresh").status_code == 200
    again = client.post("/api/smart-money/insiders/refresh", json={"symbols": ["X"]})
    assert again.status_code == 429 and "wait" in again.json()["detail"]
    assert seen == [["WL1", "WL2"]]


def test_crypto_is_left_out_of_the_default_refresh_list(monkeypatch):
    class Entry:
        def __init__(self, symbol):
            self.symbol = symbol

    monkeypatch.setattr(
        service.universe, "load_universe", lambda: [Entry("AAPL"), Entry("BTC-USD"), Entry("MSFT")]
    )
    assert service.default_refresh_symbols(10) == ["AAPL", "MSFT"]
    assert service.default_refresh_symbols(1) == ["AAPL"]
