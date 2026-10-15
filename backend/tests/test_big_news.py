"""Big-news buy lane: pure matcher rules, then the scan (no gate, reduced size, shared per-scan cap)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from app.analysis import big_news
from app.analysis.big_news import classify_headline, evaluate_headlines
from app.config import AppSettings
from app.data_providers.base import NewsItem
from app.portfolio.models import TradePlanRecord

from tests.test_hold_buy import Provider, session  # noqa: F401 - fixtures

NOW = datetime(2026, 10, 16, 15, 0)


@pytest.mark.parametrize("headline,category", [
    ("FDA approves Nova's lung drug", "FDA approval"),
    ("Nova wins $2 billion Pentagon contract", "contract award"),
    ("Nova agrees to be acquired by Big Corp in $9B deal", "takeover offer"),
    ("Nova raises full-year guidance after strong quarter", "guidance raise / earnings beat"),
    ("Nova beats earnings estimates", "guidance raise / earnings beat"),
    ("Nova announces $5 billion share buyback", "large buyback"),
    ("Analyst upgrades Nova to buy", "analyst upgrade"),
    ("Nova opens new office", None),
    ("Nova to acquire Small Co", None),  # the company is the buyer, not the target
])
def test_classify(headline, category):
    assert classify_headline(headline) == category


def test_analyst_upgrades_switch_off(monkeypatch):
    monkeypatch.setattr(big_news, "INCLUDE_ANALYST_UPGRADES", False)
    assert classify_headline("Analyst upgrades Nova to buy") is None


def test_negative_headline_blocks_a_big_one():
    items = [("Nova files for bankruptcy protection", NOW - timedelta(hours=2)), ("FDA approves Nova drug", NOW - timedelta(hours=1))]
    hit, blocked = evaluate_headlines(items, NOW, 24)
    assert hit is None and "bankruptcy" in blocked
    # an old negative (outside the window) does not block
    hit, blocked = evaluate_headlines([("Nova delisting notice", NOW - timedelta(days=10)), items[1]], NOW, 24)
    assert hit is not None and blocked is None
    # an undated negative still blocks; an undated big headline never counts
    assert evaluate_headlines([("Nova going concern doubt", None)], NOW, 24)[1] is not None
    assert evaluate_headlines([("FDA approves Nova drug", None)], NOW, 24) == (None, None)


def test_old_big_headline_is_ignored():
    assert evaluate_headlines([("FDA approves Nova drug", NOW - timedelta(hours=30))], NOW, 24) == (None, None)


# ------------------------------------------------------------------ the scan


def _fresh():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


class NewsProvider(Provider):
    def get_news(self, symbol, limit=5):
        if symbol == "BAD":
            return [NewsItem("BAD wins big contract", "x", "u", _fresh()), NewsItem("BAD faces delisting notice", "x", "u", _fresh())]
        if symbol == "QUIET":
            return [NewsItem("QUIET opens office", "x", "u", _fresh())]
        return [NewsItem(f"FDA approves {symbol} drug", "x", "u", _fresh())]


def _scan(session, monkeypatch, settings, watchlist, gate_passes=False):
    from app.analysis.quality_gate import GateResult
    from app.services import automation_service, hold_buy_service

    monkeypatch.setattr(automation_service, "get_default_watchlist", lambda n: watchlist)
    monkeypatch.setattr(automation_service, "notify", lambda *a, **k: None)
    monkeypatch.setattr(hold_buy_service, "check_quality", lambda dp, symbol, **kw: GateResult(gate_passes, [] if gate_passes else [__import__("app.analysis.quality_gate", fromlist=["GateCheck"]).GateCheck("profit", False, "no")]))
    return automation_service.run_hold_scan(settings, NewsProvider(), session)


def test_off_by_default(session, monkeypatch):
    assert _scan(session, monkeypatch, AppSettings(), ["AAA"]) == []


def test_buys_without_the_gate_at_reduced_size_and_names_the_headline(session, monkeypatch):
    bought = _scan(session, monkeypatch, AppSettings(big_news_buy_enabled=True), ["AAA", "QUIET", "BAD"])
    assert bought == ["AAA"]  # QUIET has no big news, BAD has a negative hit
    plan = session.exec(select(TradePlanRecord).where(TradePlanRecord.symbol == "AAA")).one()
    assert "[big news lane]" in plan.reason and "FDA approves AAA drug" in plan.reason
    assert plan.suggested_shares == 22  # half of the 4.5% slice at $100 on 100k


def test_shares_the_per_scan_cap(session, monkeypatch):
    settings = AppSettings(big_news_buy_enabled=True, hold_max_new_per_scan=2)
    assert _scan(session, monkeypatch, settings, ["AAA", "BBB", "CCC"]) == ["AAA", "BBB"]


def test_quality_lane_buys_first_and_counts_toward_the_cap(session, monkeypatch):
    settings = AppSettings(big_news_buy_enabled=True, hold_max_new_per_scan=1)
    assert _scan(session, monkeypatch, settings, ["AAA", "BBB"], gate_passes=True) == ["AAA"]
    plan = session.exec(select(TradePlanRecord)).one()
    assert "[quality lane]" in plan.reason


def test_found_stock_is_adopted_once_and_alerted(session, monkeypatch):
    from app.data_providers import universe_store
    from app.services import market_find_service

    sent = []
    monkeypatch.setattr(market_find_service, "notify", lambda tok, chat, text: sent.append(text))
    settings = AppSettings(big_news_buy_enabled=True)
    assert _scan(session, monkeypatch, settings, ["ZZNEW"]) == ["ZZNEW"]
    symbols = [s.symbol for s in universe_store.read_store().watchlist.symbols]
    assert symbols.count("ZZNEW") == 1
    assert "failed the quality gate" in sent[0] and "ZZNEW" in sent[0]
    from app.services.watchlist_service import add_to_universe
    assert add_to_universe("ZZNEW", NewsProvider()) is False  # idempotent


def test_big_8k_from_the_market_wide_feed_is_a_candidate(session, monkeypatch):
    from app.data_providers.sec_8k import Filing8K, ingest_8k_filing  # noqa: F401
    from app.knowledge import FactKind, record_fact
    from app.services.big_news_service import market_8k_symbols

    record_fact(session, kind=FactKind.SEC_FILING_8K, source="t", symbol="OUTSIDE", dedupe_key="8k:o1",
                known_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2),
                payload={"accession": "o1", "form": "8-K", "items": ["1.01"]})
    assert market_8k_symbols(session, AppSettings()) == ["OUTSIDE"]
