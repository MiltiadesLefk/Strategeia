"""Price alerts: the condition maths, cooldown and one-shot rules, the check job (fresh
quotes only, closed markets skipped, Telegram optional), and the API's validation and cap.
No network: fake data provider, notifier spy, in-memory database."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_session
from app.config import AppSettings
from app.data_providers import cache as provider_cache
from app.data_providers.base import AllProvidersFailedError, QuoteData
from app.knowledge import FactKind, facts_known_as_of
from app.main import app
from app.portfolio.alert_models import NotificationLog, PriceAlert
from app.portfolio.models import PaperPosition
from app.services import price_alert_service as pas
from app.services.notification_text import SendOutcome

OPEN_NOW = datetime(2026, 9, 29, 15, 0)  # Tuesday 11:00 ET, mid-session
SUNDAY = datetime(2026, 9, 27, 15, 0)

TELEGRAM = {"telegram_bot_token": "123:abc", "telegram_chat_id": "42"}


def settings(**kw) -> AppSettings:
    return AppSettings(**kw)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def bars(atr_like: float = 2.0, n: int = 40) -> pd.DataFrame:
    """Daily bars whose true range is about `atr_like` every day."""
    close = [100.0] * n
    return pd.DataFrame(
        {"open": close, "high": [c + atr_like / 2 for c in close], "low": [c - atr_like / 2 for c in close], "close": close, "volume": [1e6] * n}
    )


class FakeData:
    name = "fake"

    def __init__(self, prices: dict[str, float] | None = None, change: float = 0.0, failing: set[str] | None = None, atr: float = 2.0):
        self.prices = prices or {}
        self.change = change
        self.failing = failing or set()
        self.atr = atr
        self.quote_calls: list[str] = []
        self.fresh_flags: list[bool] = []

    def get_quote(self, symbol):
        self.quote_calls.append(symbol)
        self.fresh_flags.append(provider_cache._fresh_only.get())
        if symbol in self.failing or symbol not in self.prices:
            raise AllProvidersFailedError(symbol)
        return QuoteData(symbol, self.prices[symbol], self.change, 1e6, 1e6)

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        return bars(self.atr)


class Spy:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.texts: list[str] = []

    def __call__(self, cfg, text):
        self.texts.append(text)
        return SendOutcome(self.ok, 1, "x")


def long_position(session, symbol="AAA", entry=100.0, stop=95.0, tp1=110.0) -> PaperPosition:
    p = PaperPosition(symbol=symbol, direction="long", entry_price=entry, stop_loss=stop, tp1=tp1, tp2=tp1 + 5, shares=10)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


# ------------------------------------------------------------------ condition maths


@pytest.mark.parametrize(
    "condition, threshold, price, expected",
    [
        ("price_above", 100, 100, True),
        ("price_above", 100, 99.99, False),
        ("price_below", 100, 100, True),
        ("price_below", 100, 100.01, False),
    ],
)
def test_price_level_conditions_include_the_level_itself(condition, threshold, price, expected):
    assert pas.evaluate_condition(condition, threshold, None, price=price).met is expected


def test_day_move_counts_a_fall_as_well_as_a_rise_and_needs_a_change():
    assert pas.evaluate_condition("day_move_pct", 3, None, price=10, change_pct=-3.2).met is True
    assert pas.evaluate_condition("day_move_pct", 3, None, price=10, change_pct=2.9).met is False
    assert pas.evaluate_condition("day_move_pct", 3, None, price=10, change_pct=None).met is None


def test_near_stop_in_percent_for_a_long_and_a_short():
    # Long, stop 95: at 96 the stop is 1.04% away.
    long_result = pas.evaluate_condition("near_stop", 1.5, "pct", price=96, direction="long", stop=95, tp1=110)
    assert long_result.met is True and long_result.value == pytest.approx(1 / 96 * 100)
    assert pas.evaluate_condition("near_stop", 1.0, "pct", price=96, direction="long", stop=95, tp1=110).met is False
    # Short, stop 105: at 104 the stop is 1 above the price.
    assert pas.evaluate_condition("near_stop", 1.5, "pct", price=104, direction="short", stop=105, tp1=90).met is True
    assert pas.evaluate_condition("near_stop", 1.5, "pct", price=100, direction="short", stop=105, tp1=90).met is False


def test_near_stop_in_atr_uses_the_average_daily_range():
    # 3 away with an ATR of 2 is 1.5 ATR.
    near = pas.evaluate_condition("near_stop", 1.5, "atr", price=98, direction="long", stop=95, atr=2.0)
    assert near.met is True and near.value == pytest.approx(1.5)
    assert pas.evaluate_condition("near_stop", 1.4, "atr", price=98, direction="long", stop=95, atr=2.0).met is False
    # No ATR yet: cannot judge, not "false".
    assert pas.evaluate_condition("near_stop", 1.5, "atr", price=98, direction="long", stop=95, atr=None).met is None


def test_near_tp1_for_a_long_and_a_short_and_a_price_already_past_it():
    assert pas.evaluate_condition("near_tp1", 1.0, "pct", price=109, direction="long", stop=95, tp1=110).met is True
    assert pas.evaluate_condition("near_tp1", 1.0, "pct", price=105, direction="long", stop=95, tp1=110).met is False
    assert pas.evaluate_condition("near_tp1", 1.0, "pct", price=90.5, direction="short", stop=105, tp1=90).met is True
    through = pas.evaluate_condition("near_tp1", 1.0, "pct", price=112, direction="long", stop=95, tp1=110)
    assert through.met is True and "reached" in through.detail
    through_stop = pas.evaluate_condition("near_stop", 1.0, "pct", price=94, direction="long", stop=95)
    assert through_stop.met is True


def test_a_position_condition_without_a_position_cannot_be_judged():
    assert pas.evaluate_condition("near_stop", 1, "pct", price=10).met is None


def test_cooldown_and_one_shot_rules():
    base = dict(symbol="A", condition="price_above", threshold=1.0, cooldown_minutes=60)
    fresh = PriceAlert(**base)
    assert pas.cooldown_elapsed(fresh, OPEN_NOW) is True
    fired_once = PriceAlert(**base, last_triggered_at=OPEN_NOW - timedelta(days=3))
    assert pas.cooldown_elapsed(fired_once, OPEN_NOW) is False  # one-shot never repeats
    repeating = PriceAlert(**base, repeat=True, last_triggered_at=OPEN_NOW - timedelta(minutes=59))
    assert pas.cooldown_elapsed(repeating, OPEN_NOW) is False
    repeating.last_triggered_at = OPEN_NOW - timedelta(minutes=60)
    assert pas.cooldown_elapsed(repeating, OPEN_NOW) is True
    assert pas.cooldown_elapsed(PriceAlert(**base, status="cancelled"), OPEN_NOW) is False


# ------------------------------------------------------------------ the check


def test_a_one_shot_alert_fires_once_records_a_fact_and_messages(session):
    pas.create_alert(session, symbol="aaa", condition="price_above", threshold=150, note="breakout")
    spy = Spy()
    data = FakeData({"AAA": 151.0}, change=1.3)
    result = pas.run_price_alert_check(session, settings(**TELEGRAM), data, OPEN_NOW, notifier=spy)
    assert result.fired == ["AAA:price_above"] and result.messages_sent == 1
    assert "AAA" in spy.texts[0] and "151.00" in spy.texts[0] and "breakout" in spy.texts[0]
    assert "nothing was traded" in spy.texts[0]
    alert = session.exec(select(PriceAlert)).one()
    assert alert.status == "triggered" and alert.trigger_count == 1 and alert.last_value == 151.0
    facts = facts_known_as_of(session, FactKind.PRICE_ALERT, as_of=OPEN_NOW + timedelta(minutes=1))
    assert len(facts) == 1 and facts[0].symbol == "AAA" and facts[0].payload["condition"] == "price_above"
    # Second tick: nothing more.
    again = pas.run_price_alert_check(session, settings(**TELEGRAM), data, OPEN_NOW + timedelta(minutes=5), notifier=spy)
    assert again.fired == [] and len(spy.texts) == 1


def test_a_repeating_alert_waits_out_its_cooldown(session):
    pas.create_alert(session, symbol="AAA", condition="price_above", threshold=150, repeat=True, cooldown_minutes=60)
    spy = Spy()
    data = FakeData({"AAA": 151.0})
    cfg = settings(**TELEGRAM)
    pas.run_price_alert_check(session, cfg, data, OPEN_NOW, notifier=spy)
    pas.run_price_alert_check(session, cfg, data, OPEN_NOW + timedelta(minutes=30), notifier=spy)
    assert len(spy.texts) == 1
    pas.run_price_alert_check(session, cfg, data, OPEN_NOW + timedelta(minutes=61), notifier=spy)
    assert len(spy.texts) == 2
    alert = session.exec(select(PriceAlert)).one()
    assert alert.status == "active" and alert.trigger_count == 2


def test_quotes_are_read_inside_fresh_data_only_and_a_failed_quote_skips_the_symbol(session):
    pas.create_alert(session, symbol="AAA", condition="price_below", threshold=200)
    pas.create_alert(session, symbol="BBB", condition="price_below", threshold=200)
    spy = Spy()
    data = FakeData({"BBB": 100.0}, failing={"AAA"})
    result = pas.run_price_alert_check(session, settings(**TELEGRAM), data, OPEN_NOW, notifier=spy)
    assert data.fresh_flags and all(data.fresh_flags)
    assert result.skipped_symbols == ["AAA"] and result.fired == ["BBB:price_below"]
    statuses = {a.symbol: a.status for a in session.exec(select(PriceAlert)).all()}
    assert statuses == {"AAA": "active", "BBB": "triggered"}  # the failed one is still waiting


def test_closed_market_symbols_are_skipped_but_crypto_is_always_checked(session):
    pas.create_alert(session, symbol="AAA", condition="price_above", threshold=1)
    pas.create_alert(session, symbol="BTC-USD", condition="price_above", threshold=1)
    data = FakeData({"AAA": 10.0, "BTC-USD": 10.0})
    result = pas.run_price_alert_check(session, settings(), data, SUNDAY, notifier=Spy())
    assert data.quote_calls == ["BTC-USD"]
    assert result.fired == ["BTC-USD:price_above"] and "AAA" in result.skipped_symbols


def test_without_telegram_the_event_is_recorded_and_nothing_is_sent(session):
    pas.create_alert(session, symbol="AAA", condition="price_above", threshold=1)
    spy = Spy()
    result = pas.run_price_alert_check(session, settings(), FakeData({"AAA": 10.0}), OPEN_NOW, notifier=spy)
    assert result.fired and spy.texts == [] and result.messages_sent == 0
    assert len(facts_known_as_of(session, FactKind.PRICE_ALERT, as_of=OPEN_NOW + timedelta(minutes=1))) == 1


def test_a_failed_send_does_not_unfire_the_alert(session):
    pas.create_alert(session, symbol="AAA", condition="price_above", threshold=1)
    spy = Spy(ok=False)
    result = pas.run_price_alert_check(session, settings(**TELEGRAM), FakeData({"AAA": 10.0}), OPEN_NOW, notifier=spy)
    assert result.fired and result.messages_sent == 0
    assert session.exec(select(PriceAlert)).one().status == "triggered"


def test_a_user_stop_alert_in_atr_uses_the_open_position_and_fetches_the_atr(session):
    long_position(session, "AAA", entry=100, stop=95, tp1=110)
    pas.create_alert(session, symbol="AAA", condition="near_stop", threshold=1.5, unit="atr")
    spy = Spy()
    # ATR is 2; price 97.5 is 2.5 from the stop = 1.25 ATR.
    result = pas.run_price_alert_check(session, settings(**TELEGRAM, price_alert_positions_enabled=False), FakeData({"AAA": 97.5}), OPEN_NOW, notifier=spy)
    assert result.fired == ["AAA:near_stop"] and "ATR" in spy.texts[0]


def test_automatic_position_alerts_fire_once_per_day_and_follow_the_settings(session):
    position = long_position(session, "AAA", entry=100, stop=95, tp1=110)
    spy = Spy()
    cfg = settings(**TELEGRAM, price_alert_stop_atr=1.0, price_alert_tp1_pct=1.0)
    # Price 96.5: 1.5 from the stop with ATR 2 = 0.75 ATR (within 1); TP1 is 14% away.
    data = FakeData({"AAA": 96.5})
    first = pas.run_price_alert_check(session, cfg, data, OPEN_NOW, notifier=spy)
    assert first.fired == ["AAA:near_stop"] and "long position" in spy.texts[0]
    second = pas.run_price_alert_check(session, cfg, data, OPEN_NOW + timedelta(minutes=5), notifier=spy)
    assert second.fired == [] and len(spy.texts) == 1  # once per day
    # The next trading day it can fire again.
    third = pas.run_price_alert_check(session, cfg, data, OPEN_NOW + timedelta(days=1), notifier=spy)
    assert third.fired == ["AAA:near_stop"]
    assert session.get(NotificationLog, f"position_alert:{position.id}:near_stop") is not None
    # Near the first target within 1%.
    tp = pas.run_price_alert_check(session, cfg, FakeData({"AAA": 109.5}), OPEN_NOW + timedelta(days=2), notifier=spy)
    assert tp.fired == ["AAA:near_tp1"]


def test_automatic_position_alerts_can_be_switched_off(session):
    long_position(session, "AAA")
    spy = Spy()
    data = FakeData({"AAA": 95.5})
    result = pas.run_price_alert_check(session, settings(**TELEGRAM, price_alert_positions_enabled=False), data, OPEN_NOW, notifier=spy)
    assert result.fired == [] and data.quote_calls == []


def test_the_check_never_changes_a_position(session):
    position = long_position(session, "AAA", entry=100, stop=95, tp1=110)
    pas.run_price_alert_check(session, settings(**TELEGRAM), FakeData({"AAA": 95.1}), OPEN_NOW, notifier=Spy())
    session.refresh(position)
    assert (position.status, position.stop_loss, position.tp1, position.shares) == ("open", 95.0, 110.0, 10)


def test_the_check_never_raises(session):
    pas.create_alert(session, symbol="AAA", condition="price_above", threshold=1)

    class Broken(FakeData):
        def get_quote(self, symbol):
            raise RuntimeError("boom")

    assert pas.run_price_alert_check(session, settings(), Broken(), OPEN_NOW, notifier=Spy()).fired == []


# ------------------------------------------------------------------ storing and the API


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_create_list_and_cancel_through_the_api(client):
    created = client.post("/api/alerts", json={"symbol": " nvda ", "condition": "price_above", "threshold": 150.5})
    assert created.status_code == 201
    body = created.json()
    assert body["symbol"] == "NVDA" and body["status"] == "active" and body["created_at"].endswith("Z")
    listing = client.get("/api/alerts").json()
    assert listing["active_count"] == 1 and listing["max_active"] == pas.MAX_ACTIVE_ALERTS
    assert client.delete(f"/api/alerts/{body['id']}").json() == {"id": body["id"], "result": "cancelled"}
    assert client.get("/api/alerts?status=cancelled").json()["alerts"][0]["status"] == "cancelled"
    assert client.get("/api/alerts?status=active").json()["alerts"] == []
    assert client.delete(f"/api/alerts/{body['id']}").json()["result"] == "deleted"
    assert client.delete(f"/api/alerts/{body['id']}").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"symbol": "AA PL", "condition": "price_above", "threshold": 1},
        {"symbol": "AAPL;DROP", "condition": "price_above", "threshold": 1},
        {"symbol": "AAPL", "condition": "price_above", "threshold": 0},
        {"symbol": "AAPL", "condition": "price_above", "threshold": -5},
        {"symbol": "AAPL", "condition": "teleport", "threshold": 1},
        {"symbol": "AAPL", "condition": "price_above", "threshold": 1, "cooldown_minutes": 1},
        {"symbol": "AAPL", "condition": "near_stop", "threshold": 1},  # no unit
        {"symbol": "AAPL", "condition": "near_stop", "threshold": 1, "unit": "pct"},  # no open position
    ],
)
def test_invalid_alerts_are_refused(client, payload):
    assert client.post("/api/alerts", json=payload).status_code == 422
    assert client.get("/api/alerts").json()["alerts"] == []


def test_a_stop_alert_is_accepted_when_there_is_an_open_position(client, session):
    long_position(session, "AAPL")
    r = client.post("/api/alerts", json={"symbol": "AAPL", "condition": "near_stop", "threshold": 1, "unit": "atr"})
    assert r.status_code == 201 and r.json()["unit"] == "atr"


def test_the_active_alert_cap(client, session):
    for _ in range(pas.MAX_ACTIVE_ALERTS):
        session.add(PriceAlert(symbol="AAA", condition="price_above", threshold=1.0))
    session.commit()
    r = client.post("/api/alerts", json={"symbol": "BBB", "condition": "price_above", "threshold": 1})
    assert r.status_code == 422 and "100 active alerts" in r.json()["detail"]
    # Cancelled alerts do not count against the cap.
    first = session.exec(select(PriceAlert)).first()
    assert client.delete(f"/api/alerts/{first.id}").json()["result"] == "cancelled"
    assert client.post("/api/alerts", json={"symbol": "BBB", "condition": "price_above", "threshold": 1}).status_code == 201


def test_listing_alerts_never_evaluates_or_writes(client, session):
    session.add(PriceAlert(symbol="AAA", condition="price_above", threshold=1.0))
    session.commit()
    before = session.exec(select(PriceAlert)).one().model_dump()
    client.get("/api/alerts")
    assert session.exec(select(PriceAlert)).one().model_dump() == before
