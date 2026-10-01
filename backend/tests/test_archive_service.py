"""The dated archive of news and fundamentals snapshots (app/services/archive_service.py).

What must hold: an item is saved once, stamped with the time it became public;
a revised snapshot is a new fact and an unchanged one is not; the archive can
never break (or alter) a request or a trade decision; a simulated backtest never
writes to it; and its readers can't see the future.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_session
from app.data_providers.base import CompanyOverview, FinancialYear, NewsItem
from app.knowledge import FactKind, KnownFact, as_of, record_fact
from app.main import app
from app.services import archive_service
from app.services.archive_service import (
    archive_fetched_data,
    archive_fundamentals,
    archive_news,
    archived_news,
    fundamentals_from_fact,
    get_archive_summary,
    latest_fundamentals_snapshot,
    news_items_from_facts,
    parse_published_at,
)
from app.timeutil import utcnow_naive

T_FETCH = datetime(2026, 6, 1, 15, 0)


def _make_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def session():
    with Session(_make_engine()) as s:
        yield s


def _news(headline="Apple beats estimates", url="https://example.com/a", published="2026-06-01T13:30:00Z", publisher="Wire"):
    return NewsItem(headline=headline, source=publisher, url=url, published_at=published)


def _overview(**overrides):
    values = dict(
        symbol="AAPL", name="Apple Inc.", market_cap=3.0e12, pe_ratio=30.0, revenue_ttm=4.0e11, eps_ttm=6.5,
        week52_low=150.0, week52_high=220.0,
    )
    values.update(overrides)
    return CompanyOverview(**values)


YEARS = [FinancialYear(2023, 380e9, 90e9), FinancialYear(2024, 400e9, 95e9)]


def _facts(session, kind):
    return list(session.exec(select(KnownFact).where(KnownFact.kind == kind).order_by(KnownFact.id)).all())


# --- news: known_at ------------------------------------------------------------------------


def test_news_with_a_zoned_publish_time_is_known_at_that_time(session):
    archive_news(session, "aapl", [_news(published="2026-06-01T13:30:00Z")], "yfinance", fetched_at=T_FETCH)

    (fact,) = _facts(session, FactKind.NEWS)
    assert fact.known_at == datetime(2026, 6, 1, 13, 30)
    assert fact.known_at_basis == "source"
    assert fact.fetched_at == T_FETCH
    assert fact.symbol == "AAPL"
    assert fact.source == "yfinance"
    assert fact.payload["headline"] == "Apple beats estimates"
    assert fact.payload["publisher"] == "Wire"
    assert fact.payload["url"] == "https://example.com/a"
    assert fact.payload["published_at"] == "2026-06-01T13:30:00Z"


def test_a_publish_time_with_an_offset_is_converted_to_utc(session):
    archive_news(session, "AAPL", [_news(published="2026-06-01T09:30:00-04:00")], "yfinance", fetched_at=T_FETCH)

    assert _facts(session, FactKind.NEWS)[0].known_at == datetime(2026, 6, 1, 13, 30)


@pytest.mark.parametrize("published", ["", "yesterday", "2026-06-01T09:30:00", "2026-06-01"])
def test_news_without_a_trustworthy_time_is_known_at_the_fetch_time(session, published):
    """A bare local time could be any zone; reading it as UTC could make the
    item look public hours too early, so it is stamped with our fetch time."""
    archive_news(session, "AAPL", [_news(published=published)], "yfinance", fetched_at=T_FETCH)

    (fact,) = _facts(session, FactKind.NEWS)
    assert fact.known_at == T_FETCH
    assert fact.known_at_basis == "fetched"
    assert fact.payload["published_at"] == published  # the raw string is kept either way


def test_a_publish_time_after_the_fetch_time_is_clamped_to_it(session):
    archive_news(session, "AAPL", [_news(published="2026-06-05T00:00:00Z")], "yfinance", fetched_at=T_FETCH)

    assert _facts(session, FactKind.NEWS)[0].known_at == T_FETCH


def test_parse_published_at_handles_rfc2822_and_rejects_junk():
    assert parse_published_at("Wed, 01 Jun 2026 13:30:00 +0000") == datetime(2026, 6, 1, 13, 30)
    assert parse_published_at(None) is None
    assert parse_published_at("not a date") is None


def test_news_without_a_headline_is_skipped(session):
    result = archive_news(session, "AAPL", [_news(headline="  ")], "yfinance", fetched_at=T_FETCH)

    assert result.new == 0
    assert _facts(session, FactKind.NEWS) == []


# --- news: dedupe -----------------------------------------------------------------------


def test_the_same_article_fetched_again_is_stored_once(session):
    first = archive_news(session, "AAPL", [_news()], "yfinance", fetched_at=T_FETCH)
    second = archive_news(session, "AAPL", [_news()], "yfinance", fetched_at=T_FETCH + timedelta(hours=4))

    assert (first.new, first.already_saved) == (1, 0)
    assert (second.new, second.already_saved) == (0, 1)
    (fact,) = _facts(session, FactKind.NEWS)
    assert fact.fetched_at == T_FETCH  # the first sighting is kept


def test_the_same_article_for_two_symbols_is_two_facts(session):
    archive_news(session, "AAPL", [_news()], "yfinance", fetched_at=T_FETCH)
    archive_news(session, "MSFT", [_news()], "yfinance", fetched_at=T_FETCH)

    assert len(_facts(session, FactKind.NEWS)) == 2


def test_an_item_without_a_url_dedupes_on_publisher_and_headline(session):
    item = _news(url="", published="")
    archive_news(session, "AAPL", [item, item], "yfinance", fetched_at=T_FETCH)
    archive_news(session, "AAPL", [_news(url="", published="", headline="Another one")], "yfinance", fetched_at=T_FETCH)

    assert len(_facts(session, FactKind.NEWS)) == 2


def test_a_later_fetch_with_a_time_moves_known_at_earlier_never_later(session):
    """First seen without a usable time (fetch time), later with the real
    publish time: the earlier, better-evidenced time wins."""
    archive_news(session, "AAPL", [_news(published="")], "yfinance", fetched_at=T_FETCH)
    archive_news(session, "AAPL", [_news(published="2026-06-01T13:30:00Z")], "yfinance", fetched_at=T_FETCH + timedelta(hours=1))

    (fact,) = _facts(session, FactKind.NEWS)
    assert fact.known_at == datetime(2026, 6, 1, 13, 30)
    assert fact.known_at_basis == "source"


# --- fundamentals ------------------------------------------------------------------------


def test_a_fundamentals_snapshot_is_saved_with_the_fetch_time(session):
    result = archive_fundamentals(session, "aapl", _overview(), YEARS, "yfinance", fetched_at=T_FETCH)

    assert result.new == 1
    (fact,) = _facts(session, FactKind.FUNDAMENTALS_SNAPSHOT)
    assert fact.known_at == T_FETCH
    assert fact.known_at_basis == "fetched"
    assert fact.effective_at is None
    assert fact.symbol == "AAPL"
    assert fact.payload["revenue_ttm"] == 4.0e11
    assert fact.payload["latest_fiscal_year"] == 2024
    assert fact.payload["financial_years"][1] == {"year": 2024, "revenue": 400e9, "net_income": 95e9}


def test_an_unchanged_snapshot_is_stored_once(session):
    archive_fundamentals(session, "AAPL", _overview(), YEARS, "yfinance", fetched_at=T_FETCH)
    again = archive_fundamentals(session, "AAPL", _overview(), YEARS, "yfinance", fetched_at=T_FETCH + timedelta(days=1))

    assert again.new == 0
    assert len(_facts(session, FactKind.FUNDAMENTALS_SNAPSHOT)) == 1


def test_price_driven_numbers_alone_do_not_create_a_new_snapshot(session):
    """Market cap, P/E and the 52-week range move on every tick; storing a new
    snapshot for each would archive the price, not the fundamentals."""
    archive_fundamentals(session, "AAPL", _overview(), YEARS, "yfinance", fetched_at=T_FETCH)
    archive_fundamentals(
        session, "AAPL", _overview(market_cap=3.1e12, pe_ratio=31.0, week52_high=225.0), YEARS, "yfinance",
        fetched_at=T_FETCH + timedelta(hours=3),
    )

    assert len(_facts(session, FactKind.FUNDAMENTALS_SNAPSHOT)) == 1


def test_a_revision_is_a_new_fact_with_its_own_later_known_at(session):
    archive_fundamentals(session, "AAPL", _overview(), YEARS, "yfinance", fetched_at=T_FETCH)
    later = T_FETCH + timedelta(days=90)
    revised_years = [*YEARS, FinancialYear(2025, 420e9, 100e9)]
    archive_fundamentals(session, "AAPL", _overview(revenue_ttm=4.2e11), revised_years, "yfinance", fetched_at=later)

    facts = _facts(session, FactKind.FUNDAMENTALS_SNAPSHOT)
    assert [f.known_at for f in facts] == [T_FETCH, later]
    assert facts[0].payload["revenue_ttm"] == 4.0e11  # the old one is untouched
    assert facts[1].payload["latest_fiscal_year"] == 2025


def test_numbers_that_go_back_to_an_old_value_are_a_new_fact(session):
    """A, then B, then A again: the second A must not dedupe into the first,
    or the newest snapshot a reader sees would still be B."""
    a, b = _overview(), _overview(eps_ttm=7.0)
    archive_fundamentals(session, "AAPL", a, YEARS, "yfinance", fetched_at=T_FETCH)
    archive_fundamentals(session, "AAPL", b, YEARS, "yfinance", fetched_at=T_FETCH + timedelta(days=1))
    archive_fundamentals(session, "AAPL", a, YEARS, "yfinance", fetched_at=T_FETCH + timedelta(days=2))

    assert len(_facts(session, FactKind.FUNDAMENTALS_SNAPSHOT)) == 3
    assert latest_fundamentals_snapshot(session, "AAPL", as_of=utcnow_naive()).payload["eps_ttm"] == 6.5


def test_no_overview_means_nothing_to_save(session):
    result = archive_fundamentals(session, "BTC-USD", None, [], "yfinance", fetched_at=T_FETCH)

    assert result.new == 0
    assert _facts(session, FactKind.FUNDAMENTALS_SNAPSHOT) == []


def test_nan_numbers_are_stored_as_missing(session):
    archive_fundamentals(session, "AAPL", _overview(pe_ratio=float("nan")), YEARS, "yfinance", fetched_at=T_FETCH)

    assert _facts(session, FactKind.FUNDAMENTALS_SNAPSHOT)[0].payload["pe_ratio"] is None


# --- readers: look-ahead safety ---------------------------------------------------------------


def test_archived_news_only_returns_what_was_public_at_the_cutoff(session):
    archive_news(session, "AAPL", [_news(url="u1", published="2026-05-30T10:00:00Z")], "yfinance", fetched_at=T_FETCH)
    archive_news(session, "AAPL", [_news(url="u2", published="2026-06-01T10:00:00Z")], "yfinance", fetched_at=T_FETCH)

    cutoff = datetime(2026, 5, 31, 0, 0)
    assert [f.dedupe_key for f in archived_news(session, "AAPL", as_of=cutoff)] == ["AAPL|u1"]
    assert [f.dedupe_key for f in archived_news(session, "AAPL", since=datetime(2026, 5, 31))] == ["AAPL|u2"]
    assert len(archived_news(session, "AAPL", limit=1)) == 1
    with as_of(cutoff):
        assert [f.dedupe_key for f in archived_news(session, "AAPL")] == ["AAPL|u1"]


def test_latest_fundamentals_snapshot_respects_the_cutoff(session):
    archive_fundamentals(session, "AAPL", _overview(), YEARS, "yfinance", fetched_at=T_FETCH)
    archive_fundamentals(session, "AAPL", _overview(eps_ttm=7.0), YEARS, "yfinance", fetched_at=T_FETCH + timedelta(days=30))

    assert latest_fundamentals_snapshot(session, "AAPL", as_of=T_FETCH - timedelta(days=1)) is None
    assert latest_fundamentals_snapshot(session, "AAPL", as_of=T_FETCH + timedelta(days=1)).payload["eps_ttm"] == 6.5
    assert latest_fundamentals_snapshot(session, "AAPL").payload["eps_ttm"] == 7.0
    assert latest_fundamentals_snapshot(session, "MSFT") is None


def test_archived_facts_convert_back_to_scorer_shapes(session):
    archive_news(session, "AAPL", [_news()], "yfinance", fetched_at=T_FETCH)
    archive_fundamentals(session, "AAPL", _overview(), YEARS, "yfinance", fetched_at=T_FETCH)

    (item,) = news_items_from_facts(archived_news(session, "AAPL"))
    assert (item.headline, item.source, item.url) == ("Apple beats estimates", "Wire", "https://example.com/a")
    overview, years = fundamentals_from_fact(latest_fundamentals_snapshot(session, "AAPL"))
    assert overview.symbol == "AAPL" and overview.eps_ttm == 6.5 and overview.market_cap == 3.0e12
    assert [y.year for y in years] == [2023, 2024]


# --- the best-effort wrapper -------------------------------------------------------------------


def test_archive_fetched_data_saves_news_and_fundamentals(session):
    class P:
        name = "yfinance"

    result = archive_fetched_data(
        session, "AAPL", overview=_overview(), financial_years=YEARS, news=[_news()], data_provider=P()
    )

    assert result.new == 2
    assert _facts(session, FactKind.NEWS)[0].source == "yfinance"
    assert len(_facts(session, FactKind.FUNDAMENTALS_SNAPSHOT)) == 1


def test_a_failing_archive_is_swallowed_and_leaves_the_session_usable(session, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(archive_service, "record_fact", boom)

    result = archive_fetched_data(
        session, "AAPL", overview=_overview(), financial_years=YEARS, news=[_news()], data_provider=None
    )

    assert result.new == 0
    assert "archiving fetched data for AAPL failed" in caplog.text
    assert session.exec(select(KnownFact)).all() == []  # and the session still works


def test_a_database_error_mid_write_is_swallowed_and_rolled_back(session, monkeypatch):
    """A failure that poisons the session (a failed flush) must be rolled back,
    or the caller's next query would raise PendingRollbackError."""
    real = archive_service.record_fact

    def poisoned(sess, **kwargs):
        sess.add(KnownFact(kind=None, known_at=T_FETCH, source="x", dedupe_key="x"))  # NOT NULL violation
        sess.flush()
        return real(sess, **kwargs)

    monkeypatch.setattr(archive_service, "record_fact", poisoned)

    archive_fetched_data(session, "AAPL", overview=None, financial_years=[], news=[_news()], data_provider=None)

    assert session.exec(select(KnownFact)).all() == []


def test_the_archive_does_not_write_inside_a_simulated_moment(session):
    with as_of(datetime(2024, 3, 1, 21, 0)):
        result = archive_fetched_data(
            session, "AAPL", overview=_overview(), financial_years=YEARS, news=[_news()], data_provider=None
        )

    assert result.new == 0
    assert session.exec(select(KnownFact)).all() == []


def test_the_archive_leaves_a_session_with_pending_changes_alone(session):
    session.add(KnownFact(kind="other", known_at=T_FETCH, source="x", dedupe_key="pending"))  # not committed

    result = archive_fetched_data(session, "AAPL", overview=_overview(), financial_years=YEARS, news=[_news()], data_provider=None)

    assert result.new == 0
    assert session.new  # the caller's work is still pending, neither committed nor discarded


def test_no_session_means_no_archive():
    assert archive_fetched_data(None, "AAPL", overview=_overview(), financial_years=YEARS, news=[], data_provider=None).new == 0


# --- hooks: research and trade plans never break --------------------------------------------------


def test_research_saves_to_the_archive_and_survives_an_archive_failure(session, monkeypatch):
    from app.llm_providers.null_provider import NullLLMProvider
    from app.services import research_service
    from tests.test_research_service import FakeResearchProvider

    response = research_service.get_research("AAPL", FakeResearchProvider(), NullLLMProvider(), session)
    assert response.name == "Fake Corp"
    assert len(_facts(session, FactKind.NEWS)) == 1  # "2026-09-01T00:00:00" has no zone: fetch time
    assert len(_facts(session, FactKind.FUNDAMENTALS_SNAPSHOT)) == 1

    def boom(*args, **kwargs):
        raise RuntimeError("archive exploded")

    monkeypatch.setattr(archive_service, "record_fact", boom)
    again = research_service.get_research("MSFT", FakeResearchProvider(), NullLLMProvider(), session)
    assert again.name == "Fake Corp"


def test_trade_plan_generation_is_unaffected_by_an_archive_failure(session, monkeypatch):
    from app.llm_providers.null_provider import NullLLMProvider
    from app.services import trade_plan_service
    from tests.test_trade_plan_service import FakeUptrendDataProvider

    baseline = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session, allow_auto_execute=False
    )

    def boom(*args, **kwargs):
        raise RuntimeError("archive exploded")

    monkeypatch.setattr(archive_service, "record_fact", boom)
    after = trade_plan_service.generate_trade_plan(
        "MSFT", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session, allow_auto_execute=False
    )

    assert after.direction == baseline.direction
    assert after.confidence_score == baseline.confidence_score


def test_trade_plan_generation_archives_what_it_fetched(session):
    from app.llm_providers.null_provider import NullLLMProvider
    from app.services import trade_plan_service
    from tests.test_trade_plan_service import FakeUptrendDataProvider

    class WithFundamentals(FakeUptrendDataProvider):
        def get_company_overview(self, symbol):
            return _overview(symbol=symbol)

        def get_financials(self, symbol):
            from app.data_providers.base import FinancialsData

            return FinancialsData(symbol=symbol, years=YEARS)

        def get_news(self, symbol, limit=5):
            return [_news()]

    trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, WithFundamentals(), NullLLMProvider(), session, allow_auto_execute=False
    )

    assert len(_facts(session, FactKind.NEWS)) == 1
    assert len(_facts(session, FactKind.FUNDAMENTALS_SNAPSHOT)) == 1


# --- the endpoint -----------------------------------------------------------------------------------


@pytest.fixture
def client():
    engine = _make_engine()

    def _override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    with Session(engine) as seed:
        yield TestClient(app), seed
    app.dependency_overrides.pop(get_session, None)


def test_archive_endpoint_reports_counts_items_and_totals(client):
    http, seed = client
    archive_news(seed, "AAPL", [_news(url="u1"), _news(url="u2", headline="Second")], "yfinance", fetched_at=T_FETCH)
    archive_fundamentals(seed, "AAPL", _overview(), YEARS, "yfinance", fetched_at=T_FETCH)
    archive_news(seed, "MSFT", [_news(url="u3")], "yfinance", fetched_at=T_FETCH + timedelta(days=1))

    body = http.get("/api/archive/aapl").json()

    assert body["symbol"] == "AAPL"
    assert body["news_count"] == 2
    assert body["fundamentals_count"] == 1
    assert body["first_archived_at"] == "2026-06-01T15:00:00Z"
    assert len(body["recent_news"]) == 2
    assert body["recent_news"][0]["known_at_basis"] == "source"
    assert body["recent_news"][0]["known_at"].endswith("Z")
    assert body["recent_fundamentals"][0]["latest_fiscal_year"] == 2024
    assert body["totals"]["news_count"] == 3
    assert body["totals"]["fundamentals_count"] == 1
    assert body["totals"]["symbols"] == 2
    assert body["totals"]["last_archived_at"] == "2026-06-02T15:00:00Z"
    assert len(http.get("/api/archive/AAPL?limit=1").json()["recent_news"]) == 1


def test_archive_endpoint_for_an_unknown_symbol_is_empty_not_an_error(client):
    http, _ = client

    response = http.get("/api/archive/ZZZZ")

    assert response.status_code == 200
    body = response.json()
    assert body["news_count"] == 0 and body["first_archived_at"] is None
    assert body["recent_news"] == [] and body["totals"]["symbols"] == 0


def test_archive_endpoint_never_writes(client):
    http, seed = client
    archive_news(seed, "AAPL", [_news()], "yfinance", fetched_at=T_FETCH)
    before = [(f.id, f.known_at, f.fetched_at, f.payload) for f in _facts(seed, FactKind.NEWS)]

    for _ in range(3):
        assert http.get("/api/archive/AAPL").status_code == 200
    assert http.post("/api/archive/AAPL").status_code == 405

    seed.expire_all()
    assert [(f.id, f.known_at, f.fetched_at, f.payload) for f in _facts(seed, FactKind.NEWS)] == before
    assert len(seed.exec(select(KnownFact)).all()) == 1


def test_archive_summary_hides_facts_known_in_the_future(session):
    """A fact stamped after 'now' (only possible through direct writes) is not counted."""
    record_fact(
        session, kind=FactKind.NEWS, symbol="AAPL", source="x", dedupe_key="future",
        known_at=utcnow_naive() + timedelta(days=2), fetched_at=utcnow_naive() + timedelta(days=2),
        payload={"headline": "from tomorrow"},
    )

    summary = get_archive_summary(session, "AAPL")

    assert summary.news_count == 0 and summary.recent_news == []
