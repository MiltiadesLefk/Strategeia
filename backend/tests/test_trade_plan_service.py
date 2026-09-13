from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import AppSettings
from app.data_providers.base import AllProvidersFailedError, QuoteData
from app.llm_providers.base import LLMResult
from app.llm_providers.null_provider import NullLLMProvider
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.services import trade_plan_service


class FakeConfiguredLLMProvider:
    """A stand-in for a real, configured LLM provider (unlike
    NullLLMProvider, whose `name == "none"` makes it deliberately excluded
    from the AI Trading Overlay). Records every prompt it's asked to answer
    so tests can assert on call counts."""

    name = "fake-llm"

    def __init__(self, response_text: str = '{"stance": "bullish", "confidence": 80, "reasoning": "Strong setup."}'):
        self.response_text = response_text
        self.prompts: list[str] = []

    def is_configured(self) -> bool:
        return True

    def generate(self, prompt: str, *, max_tokens: int = 300, temperature: float = 0.4) -> LLMResult:
        self.prompts.append(prompt)
        return LLMResult(text=self.response_text, provider=self.name, latency_ms=1)


class FakeUptrendDataProvider:
    """A steep, high-volume uptrend with periodic single-day dips (keeps RSI
    off the 100 ceiling, unlike a pure straight line) — reliably classifies
    Bullish/Strong momentum with a comfortably-above-MIN_CONFIDENCE_FOR_TRADE
    score, so generate_trade_plan takes the tradeable (not no-trade) path.
    Mirrors the equivalent fixture in test_automation_service.py. Fundamentals/
    news are deliberately unavailable (AllProvidersFailedError / None) so the
    "smart" scoring layer contributes 0 and these tests stay focused on the
    technical/paper-trading path they're actually testing."""

    name = "fake"

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        n = 150
        t = np.arange(n)
        closes = 100 + t * 3.0
        closes = closes.astype(float)
        closes[t % 15 == 0] -= 8.0
        return pd.DataFrame(
            {
                "open": closes - 0.3,
                "high": closes + 1.5,
                "low": closes - 1.5,
                "close": closes,
                "volume": [5_000_000.0] * n,
            }
        )

    def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(symbol=symbol, price=547.0, change_pct_24h=1.0, volume=5_000_000.0, avg_volume_20d=1_000_000.0)

    def get_company_overview(self, symbol: str):
        raise AllProvidersFailedError("no overview in this fake")

    def get_financials(self, symbol: str):
        raise AllProvidersFailedError("no financials in this fake")

    def get_news(self, symbol: str, limit: int = 5):
        raise AllProvidersFailedError("no news in this fake")

    def get_earnings_date(self, symbol: str):
        return None

    def get_options_summary(self, symbol: str):
        return None

    def get_insider_activity(self, symbol: str):
        return None

    def get_earnings_history(self, symbol: str, limit: int = 12):
        return []


class FakeFlatDataProvider:
    """Dead-flat price series — analyze_chart classifies this Neutral (see
    test_trend.py's equivalent), so generate_trade_plan must take the
    no-trade path rather than forcing a directional plan with no real trend
    to size it against."""

    name = "fake"

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        n = 150
        closes = [100.0] * n
        return pd.DataFrame(
            {
                "open": closes,
                "high": [c + 0.1 for c in closes],
                "low": [c - 0.1 for c in closes],
                "close": closes,
                "volume": [1_000_000.0] * n,
            }
        )

    def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(symbol=symbol, price=100.0, change_pct_24h=0.0, volume=1_000_000.0, avg_volume_20d=1_000_000.0)

    def get_company_overview(self, symbol: str):
        raise AllProvidersFailedError("no overview in this fake")

    def get_financials(self, symbol: str):
        raise AllProvidersFailedError("no financials in this fake")

    def get_news(self, symbol: str, limit: int = 5):
        raise AllProvidersFailedError("no news in this fake")

    def get_earnings_date(self, symbol: str):
        return None

    def get_options_summary(self, symbol: str):
        return None

    def get_insider_activity(self, symbol: str):
        return None

    def get_earnings_history(self, symbol: str, limit: int = 12):
        return []


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


def test_market_confirmation_score_is_populated_and_folded_into_confidence(session, monkeypatch):
    """FakeUptrendDataProvider.get_ohlcv ignores symbol/period/interval, so
    the weekly and SPY fetches both come back as the same bullish shape as
    the daily chart — both confirmation checks should agree with the "long"
    direction, landing at the score's cap."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id=""),
    )

    response = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )

    assert response.direction == "long"
    assert response.market_confirmation_score == 2
    assert "weekly timeframe also bullish" in response.signal_reasons
    assert "broad market (SPY) also bullish" in response.signal_reasons

    record = session.get(TradePlanRecord, response.id)
    assert record.market_confirmation_score == 2


def test_neutral_trend_returns_and_persists_a_no_trade_decision(session, monkeypatch):
    """Quality over quantity: a symbol with no clear trend must not get a
    forced directional plan — it gets an explicit no-trade decision, and
    that decision is written to history (unlike the old behavior, which
    just returned a transient, unpersisted response)."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id=""),
    )

    response = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeFlatDataProvider(), NullLLMProvider(), session
    )

    assert response.direction is None
    assert response.status == "no_trade"
    assert response.id is not None
    assert "No clear trend" in response.reason

    record = session.get(TradePlanRecord, response.id)
    assert record.status == "no_trade"
    assert record.direction is None
    assert record.entry is None


def test_low_confidence_returns_a_no_trade_decision_without_calling_the_llm(session, monkeypatch):
    """A weak/degenerate setup (clear-enough trend, but low combined
    confidence) must also be rejected — MIN_CONFIDENCE_FOR_TRADE guards
    against forcing a mediocre plan just because *some* direction exists.
    Must not spend an LLM call on a symbol that isn't getting a plan."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id=""),
    )
    llm_calls = []

    class TrackingLLMProvider(NullLLMProvider):
        def generate(self, prompt: str, *, max_tokens: int = 300, temperature: float = 0.4):
            llm_calls.append(prompt)
            return super().generate(prompt, max_tokens=max_tokens, temperature=temperature)

    # A mild, unremarkable uptrend (not the FakeUptrendDataProvider's steep,
    # high-volume, near-overbought one) — enough for a Bullish/Weak
    # classification but a technical score too low, combined with 0
    # fundamentals/news, to clear MIN_CONFIDENCE_FOR_TRADE.
    class FakeWeakBullishDataProvider(FakeFlatDataProvider):
        def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
            n = 150
            closes = [100.0 + i * 0.05 for i in range(n)]
            return pd.DataFrame(
                {
                    "open": closes,
                    "high": [c + 0.1 for c in closes],
                    "low": [c - 0.1 for c in closes],
                    "close": closes,
                    "volume": [1_000_000.0] * n,
                }
            )

        def get_quote(self, symbol: str) -> QuoteData:
            return QuoteData(symbol=symbol, price=107.0, change_pct_24h=0.1, volume=1_000_000.0, avg_volume_20d=1_000_000.0)

    response = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeWeakBullishDataProvider(), TrackingLLMProvider(), session
    )

    assert response.direction is None
    assert response.status == "no_trade"
    assert response.confidence_score < trade_plan_service.MIN_CONFIDENCE_FOR_TRADE
    assert llm_calls == []


def test_ai_opinion_stays_empty_when_overlay_disabled(session, monkeypatch):
    """Default behavior (the setting defaults False): even with a real,
    configured LLM provider available, no opinion call happens and the
    fields stay null — the overlay is opt-in, not automatic."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", ai_trading_overlay_enabled=False),
    )
    llm = FakeConfiguredLLMProvider()

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.ai_opinion_stance is None
    assert response.ai_opinion_score is None
    assert response.ai_opinion_text is None
    assert len(llm.prompts) == 1  # only the pre-existing ai_take_text narrative call, no overlay call


def test_ai_opinion_skipped_for_null_provider_even_when_overlay_enabled(session, monkeypatch):
    """Turning the overlay on doesn't magically produce an opinion if no
    real LLM is configured — NullLLMProvider.name == 'none' is explicitly
    excluded, same guard generate_with_fallback already uses elsewhere."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", ai_trading_overlay_enabled=True),
    )

    response = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )

    assert response.ai_opinion_stance is None
    assert response.ai_opinion_score is None
    assert response.ai_opinion_text is None


def test_ai_opinion_populated_on_a_tradeable_plan_when_overlay_enabled(session, monkeypatch):
    """The overlay adds a second, separately-labeled read — it must not
    change the rule-based direction/confidence_score it sits alongside."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", ai_trading_overlay_enabled=True),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "bullish", "confidence": 80, "reasoning": "Strong setup."}')

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.direction == "long"  # rule-based decision untouched
    assert response.ai_opinion_stance == "bullish"
    assert response.ai_opinion_score == 80
    assert response.ai_opinion_text == "Strong setup."
    assert len(llm.prompts) == 2  # one for the narrative ai_take_text, one for the overlay opinion


def test_ai_opinion_includes_a_dedicated_news_assessment_and_shows_the_keyword_read_for_contrast(session, monkeypatch):
    """Goal: "when ai is enabled, it can analyze news too, not just simple
    keyword searching." The overlay prompt must hand the AI the rule-based
    engine's own keyword-matched news reasons (so it can explicitly agree,
    disagree, or improve on that shallow read) and the response must carry a
    `news_assessment` field distinct from the general `reasoning`."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", ai_trading_overlay_enabled=True),
    )

    class FakeUptrendWithNewsDataProvider(FakeUptrendDataProvider):
        def get_news(self, symbol: str, limit: int = 5):
            from app.data_providers.base import NewsItem

            return [NewsItem(headline="Company posts record revenue", source="Wire", url="https://x", published_at="2026-01-01")]

    llm = FakeConfiguredLLMProvider(
        '{"stance": "bullish", "confidence": 75, "reasoning": "Strong technical setup overall.", '
        '"news_assessment": "The headline is about a product launch, not a financial catalyst — thinner signal than the keyword match implies."}'
    )

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendWithNewsDataProvider(), llm, session)

    assert response.ai_news_assessment == (
        "The headline is about a product launch, not a financial catalyst — thinner signal than the keyword match implies."
    )
    assert response.ai_opinion_text == "Strong technical setup overall."  # distinct field, not overwritten
    # The prompt sent to the LLM must expose the rule-based (keyword) news read for contrast.
    opinion_prompt = llm.prompts[0]
    assert "Rule-based news read" in opinion_prompt
    assert "record revenue" in opinion_prompt.lower()


def test_ai_opinion_populated_on_a_no_trade_decision_when_overlay_enabled(session, monkeypatch):
    """The whole point of "take all data and add its opinion too" for a
    no-trade symbol: the overlay still runs (the no-trade path otherwise
    skips the LLM entirely to save tokens) and its own read is persisted."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", ai_trading_overlay_enabled=True),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "neutral", "confidence": 30, "reasoning": "Nothing compelling here."}')

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeFlatDataProvider(), llm, session)

    assert response.direction is None
    assert response.status == "no_trade"
    assert response.ai_opinion_stance == "neutral"
    assert response.ai_opinion_score == 30
    assert response.ai_opinion_text == "Nothing compelling here."
    assert len(llm.prompts) == 1  # only the overlay call — no ai_take_text narrative for a no-trade plan

    record = session.get(TradePlanRecord, response.id)
    assert record.ai_opinion_stance == "neutral"
    assert record.ai_opinion_score == 30


def test_ai_opinion_falls_back_to_raw_text_on_malformed_response(session, monkeypatch):
    """A provider that ignores the JSON format instruction must not crash
    generation — the raw text is kept as the opinion, stance/score just stay
    unparsed (None) rather than guessing."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", ai_trading_overlay_enabled=True),
    )
    llm = FakeConfiguredLLMProvider("I think this looks bullish overall, roughly 70% confidence.")

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.ai_opinion_stance is None
    assert response.ai_opinion_score is None
    assert response.ai_opinion_text == "I think this looks bullish overall, roughly 70% confidence."
