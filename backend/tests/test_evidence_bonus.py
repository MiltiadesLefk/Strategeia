"""Evidence bonus: pure rules, then the scan's ordering and negative-evidence skip."""
from __future__ import annotations

from sqlmodel import select

from app.analysis.evidence_bonus import BONUS_CAP, BONUS_FLOOR, EvidenceBonus, compute_bonus
from app.config import AppSettings
from app.portfolio.models import TradePlanRecord

from tests.test_hold_buy import Provider, session  # noqa: F401 - fixtures


def test_missing_data_is_zero():
    assert compute_bonus() == EvidenceBonus(0, ())


def test_parts_add_and_cap():
    assert compute_bonus(insider_buys=1).points == 1
    assert compute_bonus(insider_buys=3, insider_cluster=True).points == 2  # a cluster replaces the lone-buy point
    b = compute_bonus(insider_cluster=True, congress_buys=2, big_news=True)
    assert b.points == BONUS_CAP and b.describe() == "evidence +3: insider cluster buy, Congress buy, big news"


def test_negative_news_sets_the_floor_whatever_else_is_positive():
    assert compute_bonus(insider_cluster=True, congress_buys=1, big_news=True, negative_news=True).points == BONUS_FLOOR


def test_insider_sells_cannot_count():
    import inspect

    assert not any("sell" in p for p in inspect.signature(compute_bonus).parameters)


def _scan(session, monkeypatch, bonuses, settings):
    from app.analysis.quality_gate import GateResult
    from app.services import automation_service, evidence_service, hold_buy_service

    monkeypatch.setattr(automation_service, "get_default_watchlist", lambda n: list(bonuses))
    monkeypatch.setattr(automation_service, "notify", lambda *a, **k: None)
    monkeypatch.setattr(hold_buy_service, "check_quality", lambda dp, symbol, **kw: GateResult(True, []))
    monkeypatch.setattr(evidence_service, "evidence_for", lambda st, dp, se, sym: bonuses[sym])
    return automation_service.run_hold_scan(settings, Provider(), session)


def test_higher_bonus_buys_first_under_the_cap_and_is_named_in_the_reason(session, monkeypatch):
    bonuses = {"AAA": EvidenceBonus(0), "BBB": compute_bonus(insider_cluster=True, big_news=True), "CCC": compute_bonus(congress_buys=1)}
    assert _scan(session, monkeypatch, bonuses, AppSettings(hold_max_new_per_scan=2)) == ["BBB", "CCC"]
    plan = session.exec(select(TradePlanRecord).where(TradePlanRecord.symbol == "BBB")).one()
    assert "evidence +3: insider cluster buy, big news" in plan.reason


def test_negative_evidence_is_not_bought(session, monkeypatch):
    bonuses = {"AAA": compute_bonus(negative_news=True), "BBB": EvidenceBonus(0)}
    assert _scan(session, monkeypatch, bonuses, AppSettings()) == ["BBB"]


def test_switch_off_keeps_list_order_and_ignores_negative(session, monkeypatch):
    bonuses = {"AAA": compute_bonus(negative_news=True), "BBB": compute_bonus(insider_cluster=True)}
    assert _scan(session, monkeypatch, bonuses, AppSettings(evidence_bonus_enabled=False)) == ["AAA", "BBB"]
