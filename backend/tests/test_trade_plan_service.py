from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import AppSettings
from app.data_providers.base import AllProvidersFailedError, QuoteData
from app.llm_providers.null_provider import NullLLMProvider
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.services import trade_plan_service


class FakeUptrendDataProvider:
    """Mirrors the synthetic uptrend fixture in test_trend.py — enough bars
    and a clean enough slope that analyze_chart reliably classifies it
    Bullish, so generate_trade_plan takes the non-Neutral path. Fundamentals/
    news are deliberately unavailable (AllProvidersFailedError / None) so the
    "smart" scoring layer contributes 0 and these tests stay focused on the
    technical/paper-trading path they're actually testing."""

    name = "fake"

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        n = 150
        closes = 100 + np.arange(n) * 0.5 + 2 * np.sin(np.arange(n) / 7)
        return pd.DataFrame(
            {
                "open": closes - 0.3,
                "high": closes + 1.0,
                "low": closes - 1.0,
                "close": closes,
                "volume": [1_000_000.0] * n,
            }
        )

    def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(symbol=symbol, price=175.0, change_pct_24h=1.0, volume=1_500_000.0, avg_volume_20d=1_000_000.0)

    def get_company_overview(self, symbol: str):
        raise AllProvidersFailedError("no overview in this fake")

    def get_financials(self, symbol: str):
        raise AllProvidersFailedError("no financials in this fake")

    def get_news(self, symbol: str, limit: int = 5):
        raise AllProvidersFailedError("no news in this fake")

    def get_earnings_date(self, symbol: str):
        return None


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_generate_trade_plan_notifies_telegram_when_configured(session, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="tok", telegram_chat_id="chat123"),
    )
    monkeypatch.setattr(
        "app.services.trade_plan_service.notify_trade_plan",
        lambda token, chat_id, text: calls.append((token, chat_id, text)),
    )

    response = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )

    assert response.direction == "long"
    assert len(calls) == 1
    token, chat_id, text = calls[0]
    assert token == "tok" and chat_id == "chat123"
    assert "AAPL" in text
    assert "LONG" in text


def test_generate_trade_plan_skips_telegram_silently_when_unconfigured(session, monkeypatch):
    """No token/chat id configured -> real notify_trade_plan is a no-op, and
    generation must complete normally (never raise, never block the response)."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id=""),
    )

    response = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )

    assert response.direction == "long"


def test_regenerating_supersedes_prior_pending_plan_for_same_symbol(session, monkeypatch):
    """Regression: repeated 'Generate Trade Plan' clicks for a symbol whose
    data hasn't changed used to insert an identical duplicate row every time
    (see the History table screenshot bug) — only one pending plan per
    symbol should ever exist; regenerating discards the old one."""
    # auto-execute disabled: this test is about the pending-supersede logic
    # specifically, which needs both plans to actually stay "pending" long
    # enough to observe — auto-execution has its own dedicated tests below.
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", auto_execute_trade_plans=False),
    )

    first = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )
    second = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )

    assert first.id != second.id
    records = session.exec(select(TradePlanRecord).where(TradePlanRecord.symbol == "AAPL")).all()
    assert len(records) == 2
    statuses = {r.id: r.status for r in records}
    assert statuses[first.id] == "discarded"
    assert statuses[second.id] == "pending"


def test_regenerating_does_not_touch_other_symbols_or_executed_plans(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id=""),
    )

    nvda_plan = trade_plan_service.generate_trade_plan(
        "NVDA", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )
    executed = session.get(TradePlanRecord, nvda_plan.id)
    executed.status = "executed"
    session.add(executed)
    session.commit()

    trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session)

    nvda_after = session.get(TradePlanRecord, nvda_plan.id)
    assert nvda_after.status == "executed"


def test_auto_execute_opens_position_when_enabled(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", auto_execute_trade_plans=True),
    )

    response = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )

    assert response.status == "executed"
    positions = session.exec(select(PaperPosition).where(PaperPosition.trade_plan_id == response.id)).all()
    assert len(positions) == 1
    assert positions[0].status == "open"


def test_auto_execute_disabled_leaves_plan_pending(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", auto_execute_trade_plans=False),
    )

    response = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )

    assert response.status == "pending"
    positions = session.exec(select(PaperPosition).where(PaperPosition.trade_plan_id == response.id)).all()
    assert positions == []


def test_auto_execute_skips_gracefully_when_cash_insufficient(session, monkeypatch):
    """Regression: auto-execute must never let a too-small account turn a
    generated plan into an unhandled exception — it should just leave the
    plan pending so the user can see it and adjust Settings."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", auto_execute_trade_plans=True),
    )

    response = trade_plan_service.generate_trade_plan(
        "AAPL", 1.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )

    assert response.status == "pending"
    positions = session.exec(select(PaperPosition).where(PaperPosition.trade_plan_id == response.id)).all()
    assert positions == []


def test_auto_execute_skips_gracefully_for_symbol_with_open_position(session, monkeypatch):
    """Regression: generating (and auto-executing) a second plan for a
    symbol that already has an open position used to pyramid into it —
    a real duplicate NVDA position was observed live. Auto-execute must
    leave the second plan pending instead, same as the insufficient-cash
    case, never raise."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", auto_execute_trade_plans=True),
    )

    first = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )
    assert first.status == "executed"

    second = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )

    assert second.status == "pending"
    open_positions = session.exec(select(PaperPosition).where(PaperPosition.symbol == "AAPL", PaperPosition.status == "open")).all()
    assert len(open_positions) == 1  # still just the first


def test_generate_trade_plan_survives_telegram_api_error(session, monkeypatch):
    """A Telegram API error must be swallowed inside notify_trade_plan and
    never bubble into trade-plan generation."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="tok", telegram_chat_id="chat123"),
    )

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": False, "description": "unauthorized"}

    monkeypatch.setattr(
        "app.services.telegram_service.httpx.post", lambda url, json, timeout: FakeResponse()
    )

    response = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )

    assert response.direction == "long"
