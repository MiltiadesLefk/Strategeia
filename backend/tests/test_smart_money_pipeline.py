"""The smart-money evaluation pipeline, review queue, sleeves and caps (NEW_PLAN phases 2-3).

Synthetic bars and in-memory databases only: nothing here touches the network or the real database.
The pinned test clock is an open US session (conftest); the closed-market tests pass their own.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_app_settings, get_data_provider, get_llm_provider, get_session
from app.config import AppSettings
from app.data_providers.base import AllProvidersFailedError, EarningsHistoryEntry, QuoteData
from app.knowledge import FactKind, make_dedupe_key, record_fact
from app.llm_providers.null_provider import NullLLMProvider
from app.main import app
from app.portfolio.missed_trade_models import OUTCOME_RESOLVED, MissedTradeOutcome
from app.portfolio.missed_trade_report import build_missed_trade_report
from app.portfolio.missed_trades import classify_plan
from app.portfolio.models import DeferredEvaluation, PaperPosition, Sleeve, TradePlanRecord
from app.services import smart_money_event_review_service as event_review
from app.services import smart_money_pipeline_service as pipeline
from app.services import smart_money_review_service as review
from app.services.automation_service import run_market_open_redos
from app.services.sleeve_service import build_sleeve_engine, ensure_smart_money_sleeves, mark_all_sleeves
from app.smartmoney.liquidity import REASON_ILLIQUID, REASON_NO_PRICES, check_liquidity
from app.smartmoney.models import (
    STATUS_AUTO_BOUGHT,
    STATUS_BOUGHT,
    STATUS_EVALUATING,
    STATUS_EXPIRED,
    STATUS_NEEDS_REVIEW,
    STATUS_NEW,
    STATUS_SKIPPED,
    SmartMoneyCandidate,
    SmartMoneyEventAlert,
)
from app.smartmoney.pipeline_rules import AUTO_SLEEVE_KEY, MANUAL_SLEEVE_KEY, SMART_MONEY_EXIT_SCAN_PERIOD
from app.smartmoney.scoring import REVIEW_EXPIRY_DAYS
from tests.conftest import PINNED_OPEN_SESSION
from tests.test_automation_service import FakeUniverseProvider

NOW = PINNED_OPEN_SESSION
CLOSED_SUNDAY = datetime(2026, 9, 27, 15, 0)
COMPONENTS = [
    {"source": "insider", "points": 4.0, "reason": "ann ceo: officer/director bought, CEO/CFO"},
    {"source": "house", "points": 1.5, "reason": "A Member reported a buy (amount range as filed)"},
]


# ---------------------------------------------------------------- fixtures


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
def sent(monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr("app.services.smart_money_pipeline_service.notify", lambda token, chat, text: messages.append(text))
    monkeypatch.setattr("app.services.smart_money_event_review_service.notify", lambda token, chat, text: messages.append(text))
    return messages


def settings_(**overrides) -> AppSettings:
    base = dict(
        telegram_bot_token="", telegram_chat_id="", auto_execute_trade_plans=True, max_concurrent_positions=5,
        smart_money_max_open_positions=5, smart_money_max_new_per_week=5,
    )
    base.update(overrides)
    return AppSettings(**base)


class Bearish(FakeUniverseProvider):
    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        n = 150
        closes = 700.0 - np.arange(n) * 3.0
        closes[np.arange(n) % 15 == 0] += 8.0
        return pd.DataFrame(
            {"open": closes + 0.3, "high": closes + 1.5, "low": closes - 1.5, "close": closes, "volume": [5_000_000.0] * n}
        )


class Cheap(FakeUniverseProvider):
    """A rising symbol that trades under the $3 floor."""

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        frame = super().get_ohlcv(symbol, period, interval)
        return frame / 100.0 * 0.5  # closes 0.5 .. 2.7


class Thin(FakeUniverseProvider):
    """Over $3 but only a few thousand dollars of volume a day."""

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        frame = super().get_ohlcv(symbol, period, interval)
        frame["volume"] = 100.0
        return frame


class NoPrices(FakeUniverseProvider):
    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        raise AllProvidersFailedError("no prices for this symbol")


def add_candidate(session, symbol="AAA", status=STATUS_NEW, score=5.5, **kw) -> SmartMoneyCandidate:
    row = SmartMoneyCandidate(
        symbol=symbol, score=score, components=COMPONENTS, scored_at=kw.pop("scored_at", NOW), created_at=NOW,
        first_trigger_known_at=NOW - timedelta(days=3), expires_at=NOW + timedelta(days=14), status=status,
        scope="universe", **kw,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def evaluate(session, candidate, provider=None, settings=None, clock=None, **kw):
    return pipeline.evaluate_candidate(
        session, candidate, settings or settings_(), provider or FakeUniverseProvider(), NullLLMProvider(), clock=clock, **kw
    )


def positions_in(session, key) -> list[PaperPosition]:
    sleeve = session.exec(select(Sleeve).where(Sleeve.key == key)).first()
    return list(session.exec(select(PaperPosition).where(PaperPosition.sleeve_id == sleeve.id)).all())


# ---------------------------------------------------------------- liquidity floor


def bars(close: float, volume: float, n: int = 30) -> pd.DataFrame:
    return pd.DataFrame({"close": [close] * n, "volume": [volume] * n})


def test_liquidity_floor_passes_a_liquid_symbol():
    verdict = check_liquidity("AAA", bars(50.0, 1_000_000.0), NOW)
    assert verdict.ok and verdict.reason is None


def test_liquidity_floor_price_under_three_is_illiquid():
    verdict = check_liquidity("AAA", bars(2.9, 50_000_000.0), NOW)
    assert not verdict.ok and verdict.reason == REASON_ILLIQUID and "floor" in verdict.detail


def test_liquidity_floor_dollar_volume_under_one_million_is_illiquid():
    verdict = check_liquidity("AAA", bars(10.0, 90_000.0), NOW)  # $900k a day
    assert not verdict.ok and verdict.reason == REASON_ILLIQUID
    assert check_liquidity("AAA", bars(10.0, 100_000.0), NOW).ok  # exactly $1M passes


def test_liquidity_floor_no_or_too_few_bars_is_no_prices_never_a_guess():
    assert check_liquidity("AAA", None, NOW).reason == REASON_NO_PRICES
    assert check_liquidity("AAA", pd.DataFrame(), NOW).reason == REASON_NO_PRICES
    assert check_liquidity("AAA", bars(50.0, 1e6, n=3), NOW).reason == REASON_NO_PRICES


def test_liquidity_floor_ignores_the_bar_still_being_traded():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"]),
            "close": [50.0] * 5 + [1.0],  # today's partial bar is far under the floor
            "volume": [1e6] * 5 + [10.0],
        }
    )
    assert check_liquidity("AAA", frame, NOW).ok


# ---------------------------------------------------------------- routing


def test_a_good_rating_is_bought_automatically_in_the_auto_sleeve(session, sent):
    row = add_candidate(session)
    assert evaluate(session, row) == STATUS_AUTO_BOUGHT
    session.refresh(row)
    assert row.status == STATUS_AUTO_BOUGHT and row.decided_by == "auto" and row.sleeve_key == AUTO_SLEEVE_KEY
    held = positions_in(session, AUTO_SLEEVE_KEY)
    assert len(held) == 1 and held[0].symbol == "AAA" and held[0].direction == "long"
    assert row.position_id == held[0].id
    plan = session.get(TradePlanRecord, row.plan_id)
    assert plan.status == "executed" and plan.source == "smart_money"
    assert plan.smart_money_candidate_id == row.id and plan.smart_money_score == 5.5
    assert "insider" in plan.smart_money_components
    # nothing landed in the core sleeve or the manual one
    assert not positions_in(session, MANUAL_SLEEVE_KEY)
    assert session.exec(select(PaperPosition).where(PaperPosition.sleeve_id.is_(None))).all() == []
    assert any("auto-buy" in m and "reported filings" in m and "not a prediction" in m for m in sent)


def test_a_bad_rating_goes_to_the_review_queue_not_dropped(session, sent):
    row = add_candidate(session)
    assert evaluate(session, row, settings=settings_(min_confidence_for_trade=100)) == STATUS_NEEDS_REVIEW
    session.refresh(row)
    assert row.status == STATUS_NEEDS_REVIEW and row.reason == "rated_bad"
    assert row.expires_at == NOW + timedelta(days=REVIEW_EXPIRY_DAYS) or abs((row.expires_at - NOW).days - REVIEW_EXPIRY_DAYS) <= 1
    plan = session.get(TradePlanRecord, row.plan_id)
    assert plan.status == "no_trade" and plan.source == "smart_money" and "Confidence too low" in plan.reason
    assert positions_in(session, AUTO_SLEEVE_KEY) == []
    text = " ".join(sent)
    assert "waiting for your decision" in text and "not a prediction" in text and "smart money knows" not in text.lower()


def test_a_bearish_trend_is_a_no_trade_because_the_account_is_long_only(session, sent):
    row = add_candidate(session)
    assert evaluate(session, row, provider=Bearish()) == STATUS_NEEDS_REVIEW
    plan = session.get(TradePlanRecord, session.get(SmartMoneyCandidate, row.id).plan_id)
    assert plan.status == "no_trade" and "Long-only" in plan.reason and plan.direction is None
    assert positions_in(session, AUTO_SLEEVE_KEY) == []


def test_a_tradeable_plan_with_auto_execute_off_is_not_dropped(session, sent):
    row = add_candidate(session)
    assert evaluate(session, row, settings=settings_(auto_execute_trade_plans=False)) == STATUS_NEEDS_REVIEW
    session.refresh(row)
    assert row.reason == "tradeable_not_bought"
    assert session.get(TradePlanRecord, row.plan_id).status == "pending"
    assert positions_in(session, AUTO_SLEEVE_KEY) == []


@pytest.mark.parametrize("provider,reason", [(Cheap(), "illiquid"), (Thin(), "illiquid"), (NoPrices(), "no_prices")])
def test_illiquid_or_unpriced_candidates_are_skipped_before_any_evaluation(session, sent, provider, reason):
    row = add_candidate(session)
    assert evaluate(session, row, provider=provider) == STATUS_SKIPPED
    session.refresh(row)
    assert row.status == STATUS_SKIPPED and row.reason == reason and row.reason_detail
    assert row.plan_id is None
    assert session.exec(select(TradePlanRecord)).all() == []  # no evaluation was spent
    assert sent == []  # and nothing enters the queue or alerts


def test_an_evaluation_error_is_skipped_with_the_reason(session, sent, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(pipeline, "generate_trade_plan", boom)
    row = add_candidate(session)
    assert evaluate(session, row) == STATUS_SKIPPED
    session.refresh(row)
    assert row.reason == "evaluation_error" and "provider exploded" in row.reason_detail
    assert sent == []


def test_run_pipeline_evaluates_every_new_candidate_and_leaves_decided_ones_alone(session, sent):
    add_candidate(session, "AAA")
    add_candidate(session, "BBB")
    done = add_candidate(session, "CCC", status=STATUS_SKIPPED)
    outcome = pipeline.run_pipeline(session, settings_(), FakeUniverseProvider(), NullLLMProvider())
    assert sorted(outcome.auto_bought) == ["AAA", "BBB"]
    session.refresh(done)
    assert done.status == STATUS_SKIPPED and done.plan_id is None


def test_the_candidate_never_sets_direction_size_stop_or_targets(session, sent):
    """Two evaluations of the same data, one with a candidate behind it and one without, make the same plan."""
    from app.services.trade_plan_service import generate_trade_plan

    row = add_candidate(session, score=9.0)
    evaluate(session, row)
    smart = session.get(TradePlanRecord, session.get(SmartMoneyCandidate, row.id).plan_id)
    plain = generate_trade_plan(
        "AAA", 100_000.0, 1.0, FakeUniverseProvider(), NullLLMProvider(), session, allow_auto_execute=False,
        settings=settings_(),
    )
    assert (smart.direction, smart.entry, smart.stop, smart.tp1, smart.suggested_shares, smart.confidence_score) == (
        plain.direction, plain.entry, plain.stop, plain.tp1, plain.suggested_shares, plain.confidence_score
    )


# ---------------------------------------------------------------- market closed


def test_a_closed_market_defers_the_candidate_and_the_open_redo_evaluates_it(session, sent):
    row = add_candidate(session)
    assert evaluate(session, row, clock=lambda: CLOSED_SUNDAY) == STATUS_EVALUATING
    session.refresh(row)
    assert row.status == STATUS_EVALUATING and row.reason == "waiting_for_open"
    assert session.exec(select(TradePlanRecord)).all() == []  # nothing evaluated off a stale price
    deferral = session.exec(select(DeferredEvaluation)).one()
    assert deferral.candidate_id == row.id and deferral.symbol == "AAA" and deferral.sleeve_id is not None
    assert positions_in(session, AUTO_SLEEVE_KEY) == []

    out = run_market_open_redos(settings_(), FakeUniverseProvider(), NullLLMProvider(), session, clock=lambda: NOW)
    assert out.smart_money == ["AAA"]
    session.refresh(row)
    session.refresh(deferral)
    assert row.status == STATUS_AUTO_BOUGHT and deferral.status == "done"
    assert len(positions_in(session, AUTO_SLEEVE_KEY)) == 1


def test_the_redo_waits_until_the_session_is_fifteen_minutes_old(session, sent):
    row = add_candidate(session)
    evaluate(session, row, clock=lambda: CLOSED_SUNDAY)
    early = datetime(2026, 9, 28, 13, 35)  # Monday 09:35 ET
    out = run_market_open_redos(settings_(), FakeUniverseProvider(), NullLLMProvider(), session, clock=lambda: early)
    assert out.smart_money == []
    session.refresh(row)
    assert row.status == STATUS_EVALUATING


def test_a_queued_candidate_that_expires_is_closed(session):
    from app.smartmoney.tracker import expire_stale_candidates

    row = add_candidate(session, status=STATUS_EVALUATING)
    assert expire_stale_candidates(session, NOW + timedelta(days=REVIEW_EXPIRY_DAYS + 1)) == 1
    session.refresh(row)
    assert row.status == STATUS_EXPIRED


# ---------------------------------------------------------------- sleeves and caps


def test_sleeves_are_created_by_an_explicit_action_and_reads_do_not_create_them(session, engine):
    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override
    try:
        client = TestClient(app)
        assert client.get("/api/smart-money/review").status_code == 200
        assert session.exec(select(Sleeve)).all() == []
        first = client.post("/api/smart-money/sleeves").json()
        assert first == {"auto_key": AUTO_SLEEVE_KEY, "manual_key": MANUAL_SLEEVE_KEY, "created": True}
        assert client.post("/api/smart-money/sleeves").json()["created"] is False
    finally:
        app.dependency_overrides.clear()
    keys = {s.key for s in session.exec(select(Sleeve)).all()}
    assert {AUTO_SLEEVE_KEY, MANUAL_SLEEVE_KEY} <= keys


def test_mark_all_sleeves_sweeps_the_smart_money_sleeves(session):
    auto, manual = ensure_smart_money_sleeves(session)
    calls = []

    class Spy(FakeUniverseProvider):
        def get_ohlcv(self, symbol, period="6mo", interval="1d"):
            calls.append(period)
            return super().get_ohlcv(symbol, period, interval)

    for sleeve, symbol in ((auto, "AAA"), (manual, "BBB")):
        session.add(PaperPosition(symbol=symbol, direction="long", entry_price=100.0, stop_loss=50.0, tp1=900.0, tp2=950.0,
                                  shares=1, status="open", sleeve_id=sleeve.id, opened_at=NOW - timedelta(days=3)))
    session.commit()
    mark_all_sleeves(session, Spy(), settings_(), snapshot=False)
    assert calls.count(SMART_MONEY_EXIT_SCAN_PERIOD) == 2  # one daily-bar read per smart-money position


def test_the_open_position_cap_is_per_sleeve_and_other_sleeves_do_not_count(session, sent):
    auto, manual = ensure_smart_money_sleeves(session)
    # three open positions in the MANUAL sleeve must not use up the auto sleeve's cap of 1
    for sym in ("X1", "X2", "X3"):
        session.add(PaperPosition(symbol=sym, direction="long", entry_price=10.0, stop_loss=9.0, tp1=12.0, tp2=13.0, shares=1,
                                  status="open", sleeve_id=manual.id, opened_at=NOW - timedelta(days=30)))
    session.commit()
    s = settings_(smart_money_max_open_positions=1)
    assert pipeline.sleeve_cap_note(session, s, auto, NOW) is None
    assert "cap" in pipeline.sleeve_cap_note(session, s, manual, NOW)
    # and the auto sleeve takes one, then is full
    assert evaluate(session, add_candidate(session, "AAA"), settings=s) == STATUS_AUTO_BOUGHT
    second = add_candidate(session, "BBB")
    assert evaluate(session, second, settings=s) == STATUS_NEEDS_REVIEW
    session.refresh(second)
    assert "smart_money_max_open_positions" in second.reason_detail
    assert session.get(TradePlanRecord, second.plan_id).status == "pending"  # a good plan, held for a person


def test_the_new_per_week_cap_counts_recent_opens_even_if_closed(session, sent):
    auto, _ = ensure_smart_money_sleeves(session)
    session.add(PaperPosition(symbol="OLD", direction="long", entry_price=10.0, stop_loss=9.0, tp1=12.0, tp2=13.0, shares=1,
                              status="closed", sleeve_id=auto.id, opened_at=NOW - timedelta(days=2), closed_at=NOW - timedelta(days=1)))
    session.add(PaperPosition(symbol="ANCIENT", direction="long", entry_price=10.0, stop_loss=9.0, tp1=12.0, tp2=13.0, shares=1,
                              status="closed", sleeve_id=auto.id, opened_at=NOW - timedelta(days=20), closed_at=NOW - timedelta(days=19)))
    session.commit()
    assert pipeline.sleeve_cap_note(session, settings_(smart_money_max_new_per_week=2), auto, NOW) is None
    note = pipeline.sleeve_cap_note(session, settings_(smart_money_max_new_per_week=1), auto, NOW)
    assert note and "smart_money_max_new_per_week" in note
    row = add_candidate(session)
    assert evaluate(session, row, settings=settings_(smart_money_max_new_per_week=1)) == STATUS_NEEDS_REVIEW


def test_the_same_symbol_may_be_held_in_two_sleeves_but_only_once_in_one(session, sent):
    """Duplicate-symbol rule: per sleeve (engine unchanged). The auto buy does not block the core scan's own."""
    row = add_candidate(session, "AAA")
    assert evaluate(session, row) == STATUS_AUTO_BOUGHT
    again = add_candidate(session, "AAA")
    assert evaluate(session, again) == STATUS_NEEDS_REVIEW  # already held in this sleeve: not bought twice
    assert "already has an open position" in session.get(SmartMoneyCandidate, again.id).reason_detail
    assert len(positions_in(session, AUTO_SLEEVE_KEY)) == 1


# ---------------------------------------------------------------- the review queue (service + endpoints)


@pytest.fixture
def client(engine):
    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_app_settings] = lambda: settings_(min_confidence_for_trade=100)
    app.dependency_overrides[get_data_provider] = lambda: FakeUniverseProvider()
    app.dependency_overrides[get_llm_provider] = lambda: NullLLMProvider()
    yield TestClient(app)
    app.dependency_overrides.clear()


def queue_item(session, symbol="AAA") -> SmartMoneyCandidate:
    row = add_candidate(session, symbol)
    evaluate(session, row, settings=settings_(min_confidence_for_trade=100))
    session.refresh(row)
    assert row.status == STATUS_NEEDS_REVIEW
    return row


def test_the_review_list_keeps_the_contract_the_frontend_relies_on(client, session, sent):
    row = queue_item(session)
    body = client.get("/api/smart-money/review").json()
    assert set(body) >= {"items", "disclaimer"} and "validated" in body["disclaimer"].lower()
    item = body["items"][0]
    for key in (
        "id", "symbol", "status", "score", "components", "plan_id", "rating", "reasons_declined", "expires_at", "created_at",
        "decided_by", "decided_at", "note", "delay_note", "market_open",
    ):
        assert key in item, key
    assert item["id"] == row.id and item["status"] == "needs_review" and item["score"] == 5.5
    assert item["components"][0].keys() >= {"source", "points", "reason"}
    assert item["rating"].keys() >= {"confidence_score", "confidence_points", "confidence_points_max", "verdict_text"}
    assert "Confidence too low" in item["reasons_declined"][0]
    assert item["market_open"] is True
    assert "Reported filings" in item["delay_note"] and "knows" not in item["delay_note"].lower()
    assert item["decided_by"] is None and item["plan_id"] == row.plan_id


def test_the_list_filters_by_status_and_get_writes_nothing(client, session, sent):
    queue_item(session, "AAA")
    skipped = queue_item(session, "BBB")
    assert client.post(f"/api/smart-money/review/{skipped.id}/skip", json={"note": "meh"}).status_code == 200
    waiting = client.get("/api/smart-money/review").json()["items"]
    assert [i["symbol"] for i in waiting] == ["AAA"]
    decided = client.get("/api/smart-money/review?status=decided").json()["items"]
    assert [i["symbol"] for i in decided] == ["BBB"] and decided[0]["status"] == "skipped"
    assert [i["symbol"] for i in client.get("/api/smart-money/review?status=skipped").json()["items"]] == ["BBB"]
    before = session.exec(select(TradePlanRecord)).all()
    client.get("/api/smart-money/review")
    assert len(session.exec(select(TradePlanRecord)).all()) == len(before)


def test_buy_opens_a_position_in_the_manual_sleeve_even_for_a_bad_rating(client, session, sent):
    row = queue_item(session)
    old_plan_id = row.plan_id
    resp = client.post(f"/api/smart-money/review/{row.id}/buy")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "bought" and body["decided_by"] == "user" and body["decided_at"] and body["position_id"]
    held = positions_in(session, MANUAL_SLEEVE_KEY)
    assert len(held) == 1 and held[0].id == body["position_id"] and held[0].symbol == "AAA"
    assert positions_in(session, AUTO_SLEEVE_KEY) == []
    session.expire_all()
    plan = session.get(TradePlanRecord, body["plan_id"])
    assert plan.status == "executed" and plan.source == "smart_money_review" and plan.smart_money_candidate_id == row.id
    # the bad-rated plan that sent it to the queue is superseded: it must not stay in the missed-trades ledger
    old = session.get(TradePlanRecord, old_plan_id)
    assert old.status == "discarded" and classify_plan(old) is None


def test_buy_is_refused_when_the_sleeve_cap_is_full(client, session, sent):
    row = queue_item(session)
    app.dependency_overrides[get_app_settings] = lambda: settings_(min_confidence_for_trade=100, smart_money_max_open_positions=1)
    _, manual = ensure_smart_money_sleeves(session)
    session.add(PaperPosition(symbol="ZZZ", direction="long", entry_price=10.0, stop_loss=9.0, tp1=12.0, tp2=13.0, shares=1,
                              status="open", sleeve_id=manual.id, opened_at=NOW - timedelta(days=30)))
    session.commit()
    resp = client.post(f"/api/smart-money/review/{row.id}/buy")
    assert resp.status_code == 409 and "smart_money_max_open_positions" in resp.json()["detail"]
    session.refresh(row)
    assert row.status == STATUS_NEEDS_REVIEW  # nothing changed: the person can still skip


def test_buy_is_refused_when_there_is_no_long_trade(client, session, sent):
    row = queue_item(session)
    app.dependency_overrides[get_data_provider] = lambda: Bearish()
    resp = client.post(f"/api/smart-money/review/{row.id}/buy")
    assert resp.status_code == 409 and "no long trade" in resp.json()["detail"].lower()
    assert positions_in(session, MANUAL_SLEEVE_KEY) == []
    session.refresh(row)
    assert row.status == STATUS_NEEDS_REVIEW


def test_buy_is_refused_for_a_duplicate_in_the_sleeve_and_for_decided_or_unknown_items(client, session, sent):
    row = queue_item(session)
    assert client.post(f"/api/smart-money/review/{row.id}/buy").status_code == 200
    assert client.post(f"/api/smart-money/review/{row.id}/buy").status_code == 409  # already bought
    second = queue_item(session)  # the same symbol is now held in the manual sleeve
    resp = client.post(f"/api/smart-money/review/{second.id}/buy")
    assert resp.status_code == 409 and "already has an open position" in resp.json()["detail"]
    assert client.post("/api/smart-money/review/9999/buy").status_code == 404
    assert client.post("/api/smart-money/review/9999/skip").status_code == 404


def test_skip_stores_who_when_and_the_note(client, session, sent):
    row = queue_item(session)
    body = client.post(f"/api/smart-money/review/{row.id}/skip", json={"note": "  not my kind of stock "}).json()
    assert body["status"] == "skipped" and body["decided_by"] == "user" and body["decided_at"] and body["note"] == "not my kind of stock"
    assert client.post(f"/api/smart-money/review/{row.id}/skip").status_code == 409
    assert positions_in(session, MANUAL_SLEEVE_KEY) == []


def test_skip_without_a_body_works(client, session, sent):
    row = queue_item(session)
    assert client.post(f"/api/smart-money/review/{row.id}/skip").json()["status"] == "skipped"


def test_buy_while_the_market_is_closed_is_queued_and_filled_at_the_open(client, session, sent, monkeypatch):
    row = queue_item(session)
    monkeypatch.setattr("app.portfolio.engine.default_clock", lambda: CLOSED_SUNDAY)
    resp = client.post(f"/api/smart-money/review/{row.id}/buy")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "needs_review" and body["position_id"] is None and "queued" in body["delay_note"].lower()
    assert body["market_open"] is False
    assert positions_in(session, MANUAL_SLEEVE_KEY) == []
    deferral = session.exec(select(DeferredEvaluation)).one()
    assert deferral.candidate_id == row.id and deferral.source == "smart_money_review"
    assert client.post(f"/api/smart-money/review/{row.id}/buy").status_code == 409  # a second click is refused

    out = run_market_open_redos(
        settings_(min_confidence_for_trade=100), FakeUniverseProvider(), NullLLMProvider(), session, clock=lambda: NOW
    )
    assert out.smart_money == ["AAA"]
    session.refresh(row)
    assert row.status == STATUS_BOUGHT and row.decided_by == "user" and row.buy_queued_at is None
    assert len(positions_in(session, MANUAL_SLEEVE_KEY)) == 1


def test_skipping_a_queued_buy_cancels_it(client, session, sent, monkeypatch):
    row = queue_item(session)
    monkeypatch.setattr("app.portfolio.engine.default_clock", lambda: CLOSED_SUNDAY)
    client.post(f"/api/smart-money/review/{row.id}/buy")
    assert client.post(f"/api/smart-money/review/{row.id}/skip").json()["status"] == "skipped"
    session.expire_all()
    assert session.exec(select(DeferredEvaluation)).one().status == "skipped"
    out = run_market_open_redos(
        settings_(min_confidence_for_trade=100), FakeUniverseProvider(), NullLLMProvider(), session, clock=lambda: NOW
    )
    assert out.smart_money == [] and positions_in(session, MANUAL_SLEEVE_KEY) == []


# ---------------------------------------------------------------- expiry


def test_review_items_expire_after_the_review_window_and_cancel_a_queued_buy(session, sent):
    row = queue_item(session)
    fresh = queue_item(session, "BBB")
    pipeline._defer_candidate(session, row, ensure_smart_money_sleeves(session)[1], settings_(), NOW, source="smart_money_review")
    row.buy_queued_at = NOW
    session.add(row)
    session.commit()
    later = row.expires_at + timedelta(minutes=1)
    assert [r.symbol for r in pipeline.expire_review_items(session, later - timedelta(days=1))] == []
    expired = pipeline.expire_review_items(session, later)
    assert {r.symbol for r in expired} == {"AAA", "BBB"}
    session.refresh(row)
    session.refresh(fresh)
    assert row.status == STATUS_EXPIRED and row.decided_by == "auto" and row.buy_queued_at is None
    assert session.exec(select(DeferredEvaluation)).one().status == "skipped"
    # a decided item is never expired again, and an expired one cannot be bought
    assert pipeline.expire_review_items(session, later + timedelta(days=30)) == []
    with pytest.raises(review.ReviewConflict):
        review.skip_review_item(session, row.id)


# ---------------------------------------------------------------- hindsight (missed-trades ledger)


def test_smart_money_plans_are_tagged_in_the_missed_trade_report(session, sent):
    row = queue_item(session)
    report = build_missed_trade_report(session)
    item = next(t for t in report.trades if t.plan_id == row.plan_id)
    assert item.source == "smart_money"


def test_skipped_and_expired_review_items_feed_the_hindsight_report_apart(session, sent):
    skipped = queue_item(session, "AAA")
    expired = queue_item(session, "BBB")
    waiting = queue_item(session, "CCC")
    review.skip_review_item(session, skipped.id)
    expired.status = STATUS_EXPIRED
    session.add(expired)
    session.commit()
    session.add(MissedTradeOutcome(plan_id=skipped.plan_id, symbol="AAA", category="low_confidence", status=OUTCOME_RESOLVED,
                                   r_multiple=1.5, exit_reason="tp1_hit"))
    session.commit()
    groups = {g.key: g for g in review.hindsight(session).groups}
    assert groups["user_skipped"].n == 1 and groups["user_skipped"].resolved == 1 and groups["user_skipped"].wins == 1
    assert groups["user_skipped"].avg_r == pytest.approx(1.5) and groups["user_skipped"].small_sample is True
    assert groups["expired"].n == 1 and groups["expired"].resolved == 0 and groups["expired"].awaiting == 1
    assert groups["undecided"].n == 1
    assert waiting.id  # still waiting


def test_a_bought_item_is_not_counted_as_a_missed_trade(client, session, sent):
    row = queue_item(session)
    client.post(f"/api/smart-money/review/{row.id}/buy")
    groups = {g.key: g.n for g in review.hindsight(session).groups}
    assert groups == {"user_skipped": 0, "expired": 0, "undecided": 0}
    report = build_missed_trade_report(session)
    assert row.symbol not in {t.symbol for t in report.trades if t.source == "smart_money"}


# ---------------------------------------------------------------- the longer holding window


def weekday_bars(start: str, end: str) -> pd.DataFrame:
    days = pd.bdate_range(start, end)
    return pd.DataFrame(
        {"date": days, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 5_000_000.0}
    )


class PeriodAwareProvider:
    """Honours `period` like a real provider: 3mo is about 63 trading days, 1y about 252."""

    name = "period-aware"

    def __init__(self):
        self.full = weekday_bars("2025-09-01", "2026-09-28")
        self.periods: list[str] = []

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        if interval != "1d":
            raise AllProvidersFailedError("no hourly bars in this fake")
        self.periods.append(period)
        rows = {"3mo": 63, "6mo": 126, "1y": 252}[period]
        return self.full.tail(rows).reset_index(drop=True)

    def get_quote(self, symbol):
        return QuoteData(symbol=symbol, price=100.0, change_pct_24h=0.0, volume=5e6, avg_volume_20d=5e6)


def seed_flat_position(session, sleeve_id, symbol, opened_at):
    session.add(PaperPosition(symbol=symbol, direction="long", entry_price=100.0, planned_entry_price=100.0, stop_loss=50.0,
                              tp1=500.0, tp2=600.0, shares=10, status="open", sleeve_id=sleeve_id, opened_at=opened_at,
                              entry_day_check="daily_only"))
    session.commit()


def test_the_smart_money_time_limit_reaches_past_the_cores_three_month_window(session):
    auto, _ = ensure_smart_money_sleeves(session)
    opened = datetime(2026, 4, 1, 20, 0)  # about 125 trading bars before the last bar
    seed_flat_position(session, auto.id, "SMA", opened)
    seed_flat_position(session, None, "CORE", opened)
    provider = PeriodAwareProvider()
    closed = mark_all_sleeves(session, provider, settings_(max_holding_days=20, smart_money_max_holding_days=120), snapshot=False)
    # the smart-money position hit its 120-bar limit; the core one is unchanged from before: its 3-month
    # window does not contain the entry bar, so its time limit is (as ever) skipped rather than guessed
    assert [(p.symbol, p.close_reason) for p in closed] == [("SMA", "time_exit")]
    assert "1y" in provider.periods and "3mo" in provider.periods
    assert session.exec(select(PaperPosition).where(PaperPosition.symbol == "CORE")).one().status == "open"


def test_smart_money_limit_zero_means_no_limit(session):
    auto, _ = ensure_smart_money_sleeves(session)
    seed_flat_position(session, auto.id, "SMA", datetime(2026, 4, 1, 20, 0))
    assert mark_all_sleeves(session, PeriodAwareProvider(), settings_(smart_money_max_holding_days=0), snapshot=False) == []


def test_engines_use_the_right_window_and_limit_per_sleeve(session):
    auto, _ = ensure_smart_money_sleeves(session)
    s = settings_(max_holding_days=20, smart_money_max_holding_days=90, max_concurrent_positions=9, smart_money_max_open_positions=3)
    sm = build_sleeve_engine(session, FakeUniverseProvider(), s, auto)
    core = build_sleeve_engine(session, FakeUniverseProvider(), s, None)
    assert (sm._exit_scan_period, sm._max_holding_days, sm._max_concurrent_positions) == ("1y", 90, 3)
    assert (core._exit_scan_period, core._max_holding_days, core._max_concurrent_positions) == ("3mo", 20, 9)


def test_the_smart_money_holding_days_setting_is_capped_at_what_the_bar_window_covers():
    with pytest.raises(Exception):
        AppSettings(smart_money_max_holding_days=201)
    assert AppSettings(smart_money_max_holding_days=200).smart_money_max_holding_days == 200


# ---------------------------------------------------------------- event-exit review


def add_8k(session, symbol, accession, items, known_at):
    record_fact(
        session, kind=FactKind.SEC_FILING_8K, symbol=symbol, source="test", source_ref=f"https://example.test/{accession}",
        dedupe_key=make_dedupe_key("8k", accession), known_at=known_at, effective_at=known_at,
        payload={"accession": accession, "form": "8-K", "items": items, "symbol": symbol}, fetched_at=known_at,
    )


class EarningsProvider(FakeUniverseProvider):
    def __init__(self, history):
        self.history = history

    def get_earnings_history(self, symbol, limit=12):
        return self.history


def held(session, symbol="AAA", key=AUTO_SLEEVE_KEY, opened=NOW - timedelta(days=5)) -> PaperPosition:
    ensure_smart_money_sleeves(session)
    sleeve = session.exec(select(Sleeve).where(Sleeve.key == key)).one()
    p = PaperPosition(symbol=symbol, direction="long", entry_price=100.0, stop_loss=90.0, tp1=120.0, tp2=130.0, shares=1,
                      status="open", sleeve_id=sleeve.id, opened_at=opened)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def test_a_negative_8k_on_a_held_position_alerts_once_and_sells_nothing(session, sent):
    p = held(session)
    add_8k(session, "AAA", "0001-26-000001", ["5.02"], NOW - timedelta(days=1))
    add_8k(session, "AAA", "0001-26-000002", ["7.01"], NOW - timedelta(days=1))  # not negative-leaning
    add_8k(session, "AAA", "0001-26-000003", ["1.03"], NOW - timedelta(days=9))  # filed before the position opened
    first = event_review.run_event_review(session, settings_(), FakeUniverseProvider(), clock=lambda: NOW)
    assert len(first) == 1 and "5.02" in first[0] and "nothing was sold" in first[0]
    assert event_review.run_event_review(session, settings_(), FakeUniverseProvider(), clock=lambda: NOW) == []
    session.refresh(p)
    assert p.status == "open"
    assert len(session.exec(select(SmartMoneyEventAlert)).all()) == 1


def test_an_earnings_miss_after_entry_alerts_and_an_earlier_one_does_not(session, sent):
    held(session)
    history = [
        EarningsHistoryEntry(date=(NOW - timedelta(days=2)).date(), eps_estimate=1.0, eps_actual=0.8, surprise_pct=-20.0),
        EarningsHistoryEntry(date=(NOW - timedelta(days=60)).date(), eps_estimate=1.0, eps_actual=0.5, surprise_pct=-50.0),
        EarningsHistoryEntry(date=(NOW - timedelta(days=1)).date(), eps_estimate=1.0, eps_actual=1.1, surprise_pct=10.0),
    ]
    out = event_review.run_event_review(session, settings_(), EarningsProvider(history), clock=lambda: NOW)
    assert len(out) == 1 and "under consensus" in out[0]


def test_event_review_only_looks_at_smart_money_sleeves(session, sent):
    ensure_smart_money_sleeves(session)
    session.add(PaperPosition(symbol="AAA", direction="long", entry_price=100.0, stop_loss=90.0, tp1=120.0, tp2=130.0, shares=1,
                              status="open", sleeve_id=None, opened_at=NOW - timedelta(days=5)))
    session.commit()
    add_8k(session, "AAA", "0001-26-000009", ["5.02"], NOW - timedelta(days=1))
    assert event_review.run_event_review(session, settings_(), FakeUniverseProvider(), clock=lambda: NOW) == []


# ---------------------------------------------------------------- strategy snapshot, scheduler, invariants


def test_the_pipeline_constants_and_settings_move_the_strategy_fingerprint(monkeypatch):
    from app.smartmoney import pipeline_rules
    from app.strategy.snapshot import build_snapshot, fingerprint

    def fp(**kw):
        return fingerprint(build_snapshot(AppSettings(**kw)))

    base = fp()
    assert fp(smart_money_max_new_per_week=7) != base
    assert fp(smart_money_max_holding_days=60) != base
    monkeypatch.setattr(pipeline_rules, "NEW_POSITION_WINDOW_DAYS", 14)
    assert fp() != base


def test_the_scheduler_registers_the_pipeline_and_event_review_jobs():
    from app import scheduler

    sched = scheduler.start_scheduler()
    try:
        ids = {job.id for job in sched.get_jobs()}
        assert {"smart_money_pipeline", "smart_money_event_review"} <= ids
    finally:
        scheduler.stop_scheduler()


def test_the_pipeline_and_review_code_never_touch_direction_size_or_stops():
    """The same grep-style guard the committee has: smart-money modules read plans, they do not set levels."""
    import inspect

    from app.services import smart_money_pipeline_service as mod

    source = inspect.getsource(mod) + inspect.getsource(review) + inspect.getsource(event_review)
    for forbidden in (".stop =", ".tp1 =", ".entry =", ".direction =", ".suggested_shares ="):
        assert forbidden not in source, forbidden


def test_the_watcher_events_keep_symbol_none(session):
    from app.watchers.smart_money_watcher import candidate_event

    row = add_candidate(session)
    assert candidate_event(row).symbol is None


def test_an_auto_buy_sends_one_telegram_message_not_two(session, sent, monkeypatch):
    ordinary: list[str] = []
    monkeypatch.setattr("app.services.trade_plan_service.notify_trade_plan", lambda token, chat, text: ordinary.append(text))
    row = add_candidate(session)
    assert evaluate(session, row) == STATUS_AUTO_BOUGHT
    assert ordinary == []  # the usual "New trade plan" message is suppressed for this source
    assert len([m for m in sent if "auto-buy" in m]) == 1


def test_thesis_window_uses_the_smart_money_sleeve_time_limit(session):
    from app.portfolio.models import PaperPosition
    from app.services.thesis_service import holding_window_days, position_max_holding_days

    ensure_smart_money_sleeves(session)
    auto = session.exec(select(Sleeve).where(Sleeve.key == AUTO_SLEEVE_KEY)).one()
    cfg = settings_()
    held = PaperPosition(symbol="AAA", direction="long", sleeve_id=auto.id)
    core = PaperPosition(symbol="AAA", direction="long", sleeve_id=None)
    assert position_max_holding_days(session, held, cfg) == cfg.smart_money_max_holding_days == 120
    assert position_max_holding_days(session, core, cfg) == cfg.max_holding_days == 20
    assert holding_window_days(cfg, 120) > holding_window_days(cfg, 20)


def test_the_review_item_and_the_alert_say_when_core_also_holds_the_symbol(session, sent):
    from app.services.smart_money_review_service import held_in_core

    row = add_candidate(session, symbol="AAA")
    assert held_in_core(session, "AAA") is False
    session.add(PaperPosition(symbol="AAA", direction="long", status="open", sleeve_id=None, entry_price=10, stop_loss=9, tp1=12, tp2=14, shares=1, opened_at=NOW))
    session.commit()
    assert held_in_core(session, "AAA") is True
    assert held_in_core(session, "BBB") is False


def test_a_backlog_is_evaluated_a_fixed_number_per_run_and_none_is_dropped(session, sent):
    for i in range(7):
        add_candidate(session, f"S{i}")
    cfg = settings_(smart_money_max_evaluations_per_run=3, auto_execute_trade_plans=False)
    for expected_left in (4, 1, 0):
        pipeline.run_pipeline(session, cfg, FakeUniverseProvider(), NullLLMProvider())
        assert len(session.exec(select(SmartMoneyCandidate).where(SmartMoneyCandidate.status == STATUS_NEW)).all()) == expected_left
