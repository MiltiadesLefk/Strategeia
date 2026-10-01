"""Positions only open while the symbol's market is open (plan.md F-6), and a
plan made while it's closed is redone from fresh data at the next open (D10 = C).

The bug this guards: on Sunday 2026-09-27 at 12:28 ET, a hand-generated NVDA
plan with auto-execute on filled at Friday's close plus slippage — a price
nobody could trade at on a Sunday. SUNDAY below is that exact moment.

tests/conftest.py pins the engine's default clock to an open session for every
other test; these tests pass their own clocks (or re-pin the default) instead.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_app_settings, get_data_provider, get_session
from app.config import AppSettings
from app.data_providers.base import AllProvidersFailedError, QuoteData
from app.llm_providers.null_provider import NullLLMProvider
from app.main import app
from app.portfolio.engine import MarketClosedError, PaperTradingEngine
from app.portfolio.models import DeferredEvaluation, EquitySnapshot, PaperPosition, TradePlanRecord
from app.services import automation_service, trade_plan_service
from app.services.deferred_evaluation_service import MAX_REDO_ATTEMPTS

# Naive UTC, like every stored timestamp. New York is on EDT (UTC-4) in late
# September and EST (UTC-5) after 1 November.
SUNDAY = datetime(2026, 9, 27, 16, 28)  # Sun 12:28 ET — the F-6 fills
FRIDAY_AFTER_CLOSE = datetime(2026, 9, 25, 20, 15)  # Fri 16:15 ET, the post-close scan
MONDAY_0900 = datetime(2026, 9, 28, 13, 0)  # before the bell
MONDAY_0935 = datetime(2026, 9, 28, 13, 35)  # open, but before the 09:45 redo
MONDAY_0950 = datetime(2026, 9, 28, 13, 50)
MONDAY_1100 = datetime(2026, 9, 28, 15, 0)
MONDAY_AFTER_CLOSE = datetime(2026, 9, 28, 21, 0)  # 17:00 ET
TUESDAY_0935 = datetime(2026, 9, 29, 13, 35)
THANKSGIVING = datetime(2026, 11, 26, 15, 0)  # Thu 10:00 ET, market shut
MONDAY_REDO_DUE = datetime(2026, 9, 28, 13, 45)  # 09:45 ET

STARTING_CASH = 100_000.0


def at(moment: datetime):
    return lambda: moment


class SequenceClock:
    """Returns each moment in turn, then keeps returning the last one."""

    def __init__(self, *moments: datetime):
        self._moments = list(moments)

    def __call__(self) -> datetime:
        return self._moments.pop(0) if len(self._moments) > 1 else self._moments[0]


class UptrendProvider:
    """A steep, high-volume uptrend (the same shape test_trade_plan_service
    uses) so generate_trade_plan takes the tradeable path; everything else
    unavailable so the smart layer contributes 0. Counts OHLCV fetches so a
    test can tell a real re-evaluation happened."""

    name = "fake"

    def __init__(self):
        self.ohlcv_calls = 0

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        self.ohlcv_calls += 1
        t = np.arange(150)
        closes = (100 + t * 3.0).astype(float)
        closes[t % 15 == 0] -= 8.0
        return pd.DataFrame(
            {"open": closes - 0.3, "high": closes + 1.5, "low": closes - 1.5, "close": closes, "volume": [5e6] * 150}
        )

    def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(symbol=symbol, price=547.0, change_pct_24h=1.0, volume=5e6, avg_volume_20d=1e6)

    def get_company_overview(self, symbol: str):
        raise AllProvidersFailedError("none")

    def get_financials(self, symbol: str):
        raise AllProvidersFailedError("none")

    def get_news(self, symbol: str, limit: int = 5):
        raise AllProvidersFailedError("none")

    def get_earnings_date(self, symbol: str):
        return None

    def get_options_summary(self, symbol: str):
        return None

    def get_insider_activity(self, symbol: str):
        return None

    def get_earnings_history(self, symbol: str, limit: int = 12):
        return []


class FlatProvider(UptrendProvider):
    """Dead flat: a Neutral trend, so the evaluation is a no_trade."""

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        self.ohlcv_calls += 1
        closes = [100.0] * 150
        return pd.DataFrame(
            {"open": closes, "high": [100.1] * 150, "low": [99.9] * 150, "close": closes, "volume": [1e6] * 150}
        )

    def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(symbol=symbol, price=100.0, change_pct_24h=0.0, volume=1e6, avg_volume_20d=1e6)


class DownProvider(UptrendProvider):
    """Every source down: the evaluation itself raises."""

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        raise AllProvidersFailedError("every source is down")


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def telegram(monkeypatch):
    """Settings with auto-execute on and Telegram captured, never sent."""
    sent: list[str] = []
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", auto_execute_trade_plans=True),
    )
    monkeypatch.setattr("app.services.trade_plan_service.notify_trade_plan", lambda token, chat, text: sent.append(text))
    monkeypatch.setattr("app.services.automation_service.notify", lambda token, chat, text: sent.append(text))
    return sent


def make_plan(session: Session, **overrides) -> TradePlanRecord:
    defaults = dict(
        symbol="NVDA", direction="long", entry=100.0, stop=95.0, tp1=110.0, tp2=120.0, rr1=2.0, rr2=4.0,
        suggested_shares=10, account_risk_dollars=50.0, confidence_score=70,
    )
    defaults.update(overrides)
    plan = TradePlanRecord(**defaults)
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


class QuoteProvider:
    name = "fake"

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        return pd.DataFrame({"open": [100.0], "high": [100.0], "low": [100.0], "close": [100.0], "volume": [1e6]})

    def get_quote(self, symbol):
        return QuoteData(symbol=symbol, price=100.0, change_pct_24h=0.0, volume=1e6, avg_volume_20d=1e6)


def generate(session, provider, clock, symbol="NVDA", **kwargs):
    return trade_plan_service.generate_trade_plan(
        symbol, STARTING_CASH, 1.0, provider, NullLLMProvider(), session, clock=clock, **kwargs
    )


def deferrals(session) -> list[DeferredEvaluation]:
    return list(session.exec(select(DeferredEvaluation)).all())


# ------------------------------------------------------------------ engine


def test_open_position_refuses_an_equity_while_the_market_is_closed(session):
    plan = make_plan(session)
    engine = PaperTradingEngine(session, QuoteProvider(), STARTING_CASH, clock=at(SUNDAY))

    with pytest.raises(MarketClosedError) as exc_info:
        engine.open_position(plan)

    assert "closed (weekend)" in str(exc_info.value)
    assert "Mon 28 Sep 09:30 ET" in str(exc_info.value)
    # Refused before anything moved: no position, no cash spent, no curve point.
    assert session.exec(select(PaperPosition)).all() == []
    assert engine.get_account_state().current_cash == STARTING_CASH
    assert session.exec(select(EquitySnapshot)).all() == []
    session.refresh(plan)
    assert plan.status == "pending"


@pytest.mark.parametrize("moment", [FRIDAY_AFTER_CLOSE, THANKSGIVING, MONDAY_0900])
def test_open_position_refuses_after_the_close_on_holidays_and_before_the_bell(session, moment):
    engine = PaperTradingEngine(session, QuoteProvider(), STARTING_CASH, clock=at(moment))
    with pytest.raises(MarketClosedError):
        engine.open_position(make_plan(session))


def test_crypto_opens_any_time_and_every_engine_timestamp_uses_its_clock(session):
    engine = PaperTradingEngine(session, QuoteProvider(), STARTING_CASH, clock=at(SUNDAY))

    position = engine.open_position(make_plan(session, symbol="BTC-USD"))

    assert position.status == "open"
    assert position.opened_at == SUNDAY
    assert [s.timestamp for s in session.exec(select(EquitySnapshot)).all()] == [SUNDAY]
    closed = engine.close_position(position, 101.0, "manual")
    assert closed.closed_at == SUNDAY


def test_an_equity_opens_normally_while_the_market_is_open(session):
    engine = PaperTradingEngine(session, QuoteProvider(), STARTING_CASH, clock=at(MONDAY_1100))
    position = engine.open_position(make_plan(session))
    assert position.status == "open" and position.opened_at == MONDAY_1100


# ------------------------------------------------ auto-execute while closed


def test_auto_execute_while_closed_leaves_the_plan_pending_and_queues_a_redo(session, telegram):
    response = generate(session, UptrendProvider(), at(SUNDAY))

    assert response.direction == "long"
    assert response.status == "pending"  # never "executed"
    assert session.exec(select(PaperPosition)).all() == []
    assert "Market closed (weekend)" in response.auto_execute_note
    assert "redone from fresh data at the next open, Mon 28 Sep 09:45 ET" in response.auto_execute_note
    assert response.redo_at == MONDAY_REDO_DUE

    [deferral] = deferrals(session)
    assert (deferral.symbol, deferral.status, deferral.source, deferral.reason) == ("NVDA", "pending", "manual", "weekend")
    assert deferral.trade_plan_id == response.id
    assert deferral.due_at == MONDAY_REDO_DUE
    assert deferral.requested_at == SUNDAY
    assert (deferral.account_size, deferral.risk_pct) == (STARTING_CASH, 1.0)
    # The note is persisted, and reaches Telegram too.
    assert session.get(TradePlanRecord, response.id).auto_execute_note == response.auto_execute_note
    assert "Market closed" in telegram[0]


def test_a_plan_made_while_closed_is_deferred_even_with_auto_execute_off(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", auto_execute_trade_plans=False),
    )

    response = generate(session, UptrendProvider(), at(THANKSGIVING))

    assert response.status == "pending"
    assert "Market closed (Thanksgiving Day)" in response.auto_execute_note
    [deferral] = deferrals(session)
    assert deferral.reason == "Thanksgiving Day"
    assert deferral.due_at == datetime(2026, 11, 27, 14, 45)  # Friday 09:45 EST


def test_a_no_trade_evaluation_is_never_queued(session, telegram):
    response = generate(session, FlatProvider(), at(SUNDAY))

    assert response.status == "no_trade"
    assert deferrals(session) == []


def test_repeated_off_hours_requests_for_a_symbol_share_one_redo(session, telegram):
    first = generate(session, UptrendProvider(), at(SUNDAY))
    second = generate(session, UptrendProvider(), at(SUNDAY.replace(hour=18)), source="auto_scan")

    [deferral] = deferrals(session)
    assert deferral.trade_plan_id == second.id  # points at the newest plan
    assert deferral.source == "manual" and deferral.requested_at == SUNDAY  # the first request is kept
    assert session.get(TradePlanRecord, first.id).status == "discarded"  # superseded, as always


def test_the_bell_ringing_mid_evaluation_defers_instead_of_filling(session, telegram):
    """The session check passes at 15:59:59, the fill comes at 16:00:01."""
    closes_mid_run = SequenceClock(datetime(2026, 9, 28, 19, 59, 59), datetime(2026, 9, 28, 20, 0, 1))

    response = generate(session, UptrendProvider(), closes_mid_run)

    assert response.status == "pending"
    assert "Market closed (after the close)" in response.auto_execute_note
    assert session.exec(select(PaperPosition)).all() == []
    [deferral] = deferrals(session)
    assert deferral.due_at == datetime(2026, 9, 29, 13, 45)  # Tuesday 09:45 ET


def test_crypto_auto_executes_on_a_sunday(session, telegram):
    response = generate(session, UptrendProvider(), at(SUNDAY), symbol="BTC-USD")

    assert response.status == "executed"
    assert deferrals(session) == []


# -------------------------------------------------------- the redo at 09:45


def redo(session, provider, moment):
    settings = AppSettings(telegram_bot_token="", telegram_chat_id="")
    return automation_service.run_market_open_redos(settings, provider, NullLLMProvider(), session, clock=at(moment))


def test_the_redo_regenerates_from_fresh_data_and_the_fresh_plan_executes(session, telegram):
    stale = generate(session, UptrendProvider(), at(SUNDAY))
    provider = UptrendProvider()

    outcome = redo(session, provider, MONDAY_0950)

    assert outcome.executed == ["NVDA"]
    assert provider.ohlcv_calls > 0  # really re-evaluated, not replayed
    [position] = session.exec(select(PaperPosition)).all()
    assert position.opened_at == MONDAY_0950
    fresh = session.get(TradePlanRecord, position.trade_plan_id)
    assert fresh.id != stale.id and fresh.status == "executed"

    old = session.get(TradePlanRecord, stale.id)
    assert old.status == "discarded"  # only the fresh plan ever executes
    assert f"see plan #{fresh.id}" in old.auto_execute_note

    [deferral] = deferrals(session)
    assert (deferral.status, deferral.result_plan_id, deferral.resolved_at) == ("done", fresh.id, MONDAY_0950)
    assert "executed" in deferral.outcome
    assert any(text.startswith("Market-open redo — executed: NVDA") for text in telegram)


@pytest.mark.parametrize("moment", [SUNDAY, MONDAY_0935, MONDAY_AFTER_CLOSE, TUESDAY_0935, THANKSGIVING])
def test_the_redo_only_runs_once_a_session_is_fifteen_minutes_old(session, telegram, moment):
    """Not yet due; due but only five minutes into the session (Monday's,
    or Tuesday's after a missed Monday); due but the market is shut."""
    generate(session, UptrendProvider(), at(SUNDAY))
    provider = UptrendProvider()

    outcome = redo(session, provider, moment)

    assert outcome.handled == 0 and provider.ohlcv_calls == 0
    assert deferrals(session)[0].status == "pending"


def test_the_redo_honours_the_auto_execute_setting_at_redo_time(session, monkeypatch, telegram):
    stale = generate(session, UptrendProvider(), at(SUNDAY))
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", auto_execute_trade_plans=False),
    )

    outcome = redo(session, UptrendProvider(), MONDAY_0950)

    assert outcome.pending == ["NVDA"]
    assert session.exec(select(PaperPosition)).all() == []
    pending = session.exec(select(TradePlanRecord).where(TradePlanRecord.status == "pending")).all()
    assert [p.id for p in pending] != [stale.id] and len(pending) == 1  # the fresh plan, not the stale one


def test_a_no_trade_redo_discards_the_off_hours_plan(session, telegram):
    stale = generate(session, UptrendProvider(), at(SUNDAY))

    outcome = redo(session, FlatProvider(), MONDAY_0950)

    assert outcome.no_trade == ["NVDA"]
    assert session.get(TradePlanRecord, stale.id).status == "discarded"
    [deferral] = deferrals(session)
    assert deferral.status == "done" and "no trade" in deferral.outcome


def test_the_redo_skips_a_symbol_already_held(session, telegram):
    stale = generate(session, UptrendProvider(), at(SUNDAY))
    session.add(PaperPosition(symbol="NVDA", direction="long", entry_price=500.0, stop_loss=450.0, tp1=600.0, tp2=650.0, shares=1))
    session.commit()
    provider = UptrendProvider()

    outcome = redo(session, provider, MONDAY_0950)

    assert outcome.skipped == ["NVDA"] and provider.ohlcv_calls == 0
    assert deferrals(session)[0].status == "skipped"
    assert session.get(TradePlanRecord, stale.id).status == "discarded"


def test_the_redo_skips_a_plan_already_executed_or_discarded(session, telegram):
    stale = generate(session, UptrendProvider(), at(SUNDAY))
    plan = session.get(TradePlanRecord, stale.id)
    plan.status = "discarded"
    session.add(plan)
    session.commit()
    provider = UptrendProvider()

    outcome = redo(session, provider, MONDAY_0950)

    assert outcome.skipped == ["NVDA"] and provider.ohlcv_calls == 0
    assert deferrals(session)[0].status == "skipped"


def test_a_failing_redo_retries_then_gives_up_and_retires_the_plan(session, telegram):
    stale = generate(session, UptrendProvider(), at(SUNDAY))

    for attempt in range(1, MAX_REDO_ATTEMPTS):
        outcome = redo(session, DownProvider(), MONDAY_0950)
        assert outcome.retrying == ["NVDA"]
        [deferral] = deferrals(session)
        assert (deferral.status, deferral.attempts) == ("pending", attempt)
        assert session.get(TradePlanRecord, stale.id).status == "pending"

    outcome = redo(session, DownProvider(), MONDAY_0950)

    assert outcome.failed == ["NVDA"]
    [deferral] = deferrals(session)
    assert (deferral.status, deferral.attempts) == ("failed", MAX_REDO_ATTEMPTS)
    retired = session.get(TradePlanRecord, stale.id)
    assert retired.status == "discarded" and "Generate a fresh plan by hand" in retired.auto_execute_note


def test_a_redo_missed_while_the_app_was_down_runs_later_in_the_session(session, telegram):
    generate(session, UptrendProvider(), at(SUNDAY))
    outcome = redo(session, UptrendProvider(), datetime(2026, 9, 29, 17, 0))  # Tuesday 13:00 ET
    assert outcome.executed == ["NVDA"]


# ------------------------------------------------------------- the routes


@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def _session_override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_data_provider] = lambda: QuoteProvider()
    app.dependency_overrides[get_app_settings] = lambda: AppSettings()
    with Session(engine) as s:
        yield TestClient(app), s
    for dependency in (get_session, get_data_provider, get_app_settings):
        app.dependency_overrides.pop(dependency, None)


def test_manual_execute_returns_400_while_the_market_is_closed(api, monkeypatch):
    client, s = api
    plan = make_plan(s)
    monkeypatch.setattr("app.portfolio.engine.default_clock", at(SUNDAY))

    resp = client.post("/api/portfolio/positions", json={"trade_plan_id": plan.id})

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "market is closed (weekend)" in detail
    assert "redone from fresh data at the next open" in detail
    assert s.exec(select(PaperPosition)).all() == []


def test_manual_execute_works_while_the_market_is_open(api, monkeypatch):
    client, s = api
    plan = make_plan(s)
    monkeypatch.setattr("app.portfolio.engine.default_clock", at(MONDAY_1100))

    resp = client.post("/api/portfolio/positions", json={"trade_plan_id": plan.id})

    assert resp.status_code == 200
    assert resp.json()["opened_at"] == "2026-09-28T15:00:00Z"


def test_a_plan_awaiting_its_redo_cannot_be_executed_by_hand_even_after_the_bell(api, monkeypatch):
    client, s = api
    plan = make_plan(s)
    s.add(DeferredEvaluation(symbol="NVDA", source="manual", reason="weekend", due_at=MONDAY_REDO_DUE, trade_plan_id=plan.id))
    s.commit()
    monkeypatch.setattr("app.portfolio.engine.default_clock", at(MONDAY_0935))  # open, redo not yet run

    resp = client.post("/api/portfolio/positions", json={"trade_plan_id": plan.id})

    assert resp.status_code == 400
    assert "redone from fresh data at Mon 28 Sep 09:45 ET" in resp.json()["detail"]

    listed = {p["id"]: p for p in client.get("/api/trade-plans").json()}
    assert listed[plan.id]["redo_at"] == "2026-09-28T13:45:00Z"
    assert client.get(f"/api/trade-plans/{plan.id}").json()["redo_at"] == "2026-09-28T13:45:00Z"


@pytest.mark.parametrize(
    "moment, expected",
    [
        (
            SUNDAY,
            {"state": "closed", "is_open": False, "holiday_name": None, "next_open": "2026-09-28T13:30:00Z",
             "next_close": "2026-09-28T20:00:00Z", "next_close_is_early": False},
        ),
        (
            THANKSGIVING,
            {"state": "holiday", "is_open": False, "holiday_name": "Thanksgiving Day",
             "next_open": "2026-11-27T14:30:00Z", "next_close": "2026-11-27T18:00:00Z", "next_close_is_early": True},
        ),
        (
            MONDAY_1100,
            {"state": "open", "is_open": True, "holiday_name": None, "next_open": "2026-09-29T13:30:00Z",
             "next_close": "2026-09-28T20:00:00Z", "next_close_is_early": False},
        ),
    ],
)
def test_market_session_endpoint(monkeypatch, moment, expected):
    monkeypatch.setattr("app.api.routers.market._now", lambda: moment.replace(tzinfo=timezone.utc))

    resp = TestClient(app).get("/api/market/session")

    assert resp.status_code == 200
    body = resp.json()
    assert {key: body[key] for key in expected} == expected
    assert body["as_of"].endswith("Z")


def test_market_session_endpoint_requires_auth(monkeypatch):
    class LockedInfra:
        api_shared_secret = "s3cr3t"
        allow_unauthenticated_api = False
        session_secret = "k"

    monkeypatch.setattr("app.api.deps.get_infra_settings", lambda: LockedInfra())
    assert TestClient(app).get("/api/market/session").status_code == 401


# --------------------------------------------------------------- scheduler


def test_scheduled_auto_scan_skips_market_holidays(monkeypatch):
    from app import scheduler
    from app.markets import to_market_time

    calls = []
    monkeypatch.setattr(scheduler, "load_app_settings", lambda: AppSettings(auto_scan_enabled=True))
    monkeypatch.setattr(scheduler, "run_auto_scan", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(scheduler, "get_data_provider", lambda settings: None)
    monkeypatch.setattr(scheduler, "get_llm_provider", lambda settings: None)

    monkeypatch.setattr(scheduler, "to_market_time", lambda now=None: to_market_time(THANKSGIVING))
    scheduler._auto_scan_job()
    assert calls == []

    monkeypatch.setattr(scheduler, "to_market_time", lambda now=None: to_market_time(MONDAY_1100))
    monkeypatch.setattr(scheduler, "Session", lambda engine: _NullSession())
    scheduler._auto_scan_job()
    assert len(calls) == 1


def test_scheduled_redo_does_nothing_while_the_market_is_closed(monkeypatch):
    from app import scheduler

    calls = []
    monkeypatch.setattr(scheduler, "is_us_market_open", lambda now=None: False)
    monkeypatch.setattr(scheduler, "run_market_open_redos", lambda *a, **k: calls.append(a))
    scheduler._market_open_redo_job()
    assert calls == []


class _NullSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
