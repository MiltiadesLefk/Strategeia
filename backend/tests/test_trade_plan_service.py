from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis.ai_overlay_scoring import AI_OVERLAY_SCORE_CAP
from app.config import AppSettings
from app.data_providers.base import AllProvidersFailedError, QuoteData
from app.llm_providers.base import LLMResult
from app.llm_providers.null_provider import NullLLMProvider
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.services import trade_plan_service
from app.services.trade_plan_service import AiOpinion


class FakeConfiguredLLMProvider:
    """A stand-in for a real, configured LLM provider (unlike
    NullLLMProvider, whose `name == "none"` makes it deliberately excluded
    from the AI Trading Overlay). Records every prompt it's asked to answer
    so tests can assert on call counts."""

    name = "fake-llm"

    def __init__(self, response_text: str = '{"stance": "bullish", "confidence": 80, "reasoning": "Strong setup."}'):
        self.response_text = response_text
        self.prompts: list[str] = []
        self.tiers: list[str] = []  # the tier each call asked for, in order

    def is_configured(self) -> bool:
        return True

    def generate(
        self, prompt: str, *, max_tokens: int = 300, temperature: float = 0.4, tier: str = "routine"
    ) -> LLMResult:
        self.prompts.append(prompt)
        self.tiers.append(tier)
        return LLMResult(text=self.response_text, provider=self.name, latency_ms=1)


class FakeUptrendDataProvider:
    """A steep, high-volume uptrend with periodic single-day dips (keeps RSI
    off the 100 ceiling, unlike a pure straight line) — reliably classifies
    Bullish/Strong momentum with a comfortably-above-the-confidence-bar
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
    confidence) must also be rejected — min_confidence_for_trade guards
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
    # fundamentals/news, to clear min_confidence_for_trade.
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
    assert response.confidence_score < AppSettings().min_confidence_for_trade
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


# ------------------------------------------------------ overlay vs auto-execute


def _default_settings(**overrides) -> AppSettings:
    defaults = dict(
        telegram_bot_token="",
        telegram_chat_id="",
        ai_trading_overlay_enabled=True,
        auto_execute_trade_plans=True,
    )
    defaults.update(overrides)
    return AppSettings(**defaults)


def test_overlay_contradiction_holds_auto_execute_instead_of_opening():
    """The core behavior: a flat opposite call from the overlay must stop
    auto-execute from firing at all — the position must not exist."""
    assert trade_plan_service._overlay_contradicts_direction("long", AiOpinion(stance="bearish")) is True
    assert trade_plan_service._overlay_contradicts_direction("short", AiOpinion(stance="bullish")) is True


def test_neutral_overlay_never_contradicts():
    """Uncertainty is not contradiction — must never hold an otherwise-clean
    auto-execute, or the mild-hedging case (most of what a real LLM returns)
    would make auto-execute far less useful."""
    assert trade_plan_service._overlay_contradicts_direction("long", AiOpinion(stance="neutral")) is False
    assert trade_plan_service._overlay_contradicts_direction("short", AiOpinion(stance="neutral")) is False


def test_agreeing_overlay_never_contradicts():
    assert trade_plan_service._overlay_contradicts_direction("long", AiOpinion(stance="bullish")) is False
    assert trade_plan_service._overlay_contradicts_direction("short", AiOpinion(stance="bearish")) is False


def test_no_stance_never_contradicts():
    """Overlay off, misconfigured, or a parse failure — all read as None and
    must never hold anything."""
    assert trade_plan_service._overlay_contradicts_direction("long", AiOpinion(stance=None)) is False


def test_contradicting_overlay_holds_the_position_open(session, monkeypatch):
    """The "hold" action in isolation, with the confidence penalty off too.
    Under the default "cancel" an objection never reaches auto-execute at
    all — the evaluation already ended as a no_trade — and with scoring on,
    this fixture's 31% sits close enough to min_confidence_for_trade that
    the penalty alone takes it under. See
    test_contradicting_overlay_vetoes_the_trade_entirely and
    test_a_disagreeing_overlay_visibly_costs_confidence for those."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(ai_overlay_objection_action="hold", ai_overlay_scores_confidence=False),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "bearish", "confidence": 62, "reasoning": "Looks exhausted."}')

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.direction == "long"  # rule-based decision itself is untouched
    assert response.status == "pending"  # never became "executed"
    assert response.ai_opinion_stance == "bearish"
    assert "held" in response.auto_execute_note
    assert "bearish" in response.auto_execute_note
    assert session.exec(select(PaperPosition)).first() is None


def test_agreeing_overlay_still_auto_executes(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "bullish", "confidence": 80, "reasoning": "Agrees."}')

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.status == "executed"
    assert "Auto-executed" in response.auto_execute_note
    assert session.exec(select(PaperPosition)).first() is not None


def test_neutral_overlay_still_auto_executes(session, monkeypatch):
    """Uncertainty from the AI must not freeze an otherwise-clean plan."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "neutral", "confidence": 50, "reasoning": "Mixed signals."}')

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.status == "executed"
    assert session.exec(select(PaperPosition)).first() is not None


def test_the_objection_can_be_made_inert_while_keeping_the_overlay_on(session, monkeypatch):
    """Action "none" with scoring off: the overlay still runs and is still
    shown, it just no longer gets any say over anything."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(
            ai_overlay_objection_action="none", ai_overlay_scores_confidence=False,
        ),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "bearish", "confidence": 62, "reasoning": "Looks exhausted."}')

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.status == "executed"
    assert response.ai_opinion_stance == "bearish"  # still shown
    assert session.exec(select(PaperPosition)).first() is not None  # but didn't block


def test_overlay_off_is_completely_unaffected(session, monkeypatch):
    """No overlay opinion exists to contradict anything — behavior must be
    identical to before this feature existed."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(
            telegram_bot_token="", telegram_chat_id="",
            ai_trading_overlay_enabled=False, auto_execute_trade_plans=True,
        ),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "bullish", "confidence": 80, "reasoning": "n/a"}')

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.ai_opinion_stance is None
    assert response.status == "executed"
    assert session.exec(select(PaperPosition)).first() is not None


# --------------------------------------- overlay as part of the decision itself


def test_contradicting_overlay_vetoes_the_trade_entirely(session, monkeypatch):
    """The strongest rung, and the default once the overlay is on: a flat
    opposite call ends the evaluation as an explicit no_trade rather than
    writing a plan the engine's own second opinion expects to lose. The
    rule-based pipeline still ran in full — this is a refusal to trade, not
    the AI picking a different direction."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "bearish", "confidence": 78, "reasoning": "Looks exhausted."}')

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.status == "no_trade"
    assert response.direction is None
    assert response.entry is None and response.stop is None and response.suggested_shares is None
    assert "AI Trading Overlay" in response.reason
    assert "bearish" in response.reason and "long" in response.reason
    assert "78%" in response.reason
    assert session.exec(select(PaperPosition)).first() is None


def test_the_veto_reason_is_named_ahead_of_the_confidence_bar(session, monkeypatch):
    """When the overlay's own penalty is what pushed the score under
    min_confidence_for_trade, reporting "confidence too low" would name the
    symptom and hide the cause."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "bearish", "confidence": 90, "reasoning": "Exhausted."}')

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert "AI Trading Overlay" in response.reason
    assert "Confidence too low" not in response.reason


def test_the_veto_does_not_fire_on_a_neutral_or_agreeing_overlay(session, monkeypatch):
    """Only a flat opposite stops a trade. Uncertainty and agreement both
    leave the rule-based plan intact."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(auto_execute_trade_plans=False),
    )
    for raw in ('{"stance": "neutral", "confidence": 50, "reasoning": "Mixed."}',
                '{"stance": "bullish", "confidence": 85, "reasoning": "Agrees."}'):
        response = trade_plan_service.generate_trade_plan(
            "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), FakeConfiguredLLMProvider(raw), session
        )
        assert response.status == "pending"
        assert response.direction == "long"


def test_action_none_leaves_the_plan_intact(session, monkeypatch):
    """Action "none": the plan is written with the rule-based direction even
    though the overlay objects. Scoring is off here so the assertion is
    about the action and not about the confidence penalty reaching the same
    outcome by another route (which it does — see the next test)."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(
            ai_overlay_objection_action="none", ai_overlay_scores_confidence=False,
            auto_execute_trade_plans=False,
        ),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "bearish", "confidence": 78, "reasoning": "Exhausted."}')

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.status == "pending"
    assert response.direction == "long"
    assert response.ai_opinion_stance == "bearish"  # still produced and shown
    assert response.ai_overlay_score == 0


def test_with_no_action_the_penalty_can_still_reach_the_no_trade_decision(session, monkeypatch):
    """The confidence penalty is a genuinely independent path to no_trade,
    not a softer restatement of "cancel": this fixture clears
    min_confidence_for_trade by less than a full-cap objection is worth, so
    it falls under the bar on the score alone. The reason must say so —
    with the action set to "none", the overlay is not what refused the
    trade, the confidence bar is."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(ai_overlay_objection_action="none", auto_execute_trade_plans=False),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "bearish", "confidence": 78, "reasoning": "Exhausted."}')

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.status == "no_trade"
    assert response.ai_overlay_score == -AI_OVERLAY_SCORE_CAP
    assert "Confidence too low" in response.reason
    assert "AI Trading Overlay" not in response.reason
    assert "AI overlay disagrees" in response.signal_reasons  # but it IS attributed in the breakdown


def _evaluate(session, monkeypatch, raw_opinion: str | None, **setting_overrides) -> tuple[int, int | None]:
    """Runs one full evaluation against the same fixture data and returns
    (confidence_score, ai_overlay_score), so a test can compare an overlay
    run against the rule-based baseline instead of hardcoding a number that
    every future scoring dimension would break."""
    overrides = dict(auto_execute_trade_plans=False, **setting_overrides)
    if raw_opinion is None:
        overrides["ai_trading_overlay_enabled"] = False
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(**overrides),
    )
    llm = FakeConfiguredLLMProvider(raw_opinion or '{"stance": "neutral", "confidence": 50, "reasoning": "n/a"}')
    response = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session
    )
    return response.confidence_score, response.ai_overlay_score


def test_a_disagreeing_overlay_visibly_costs_confidence(session, monkeypatch):
    """The whole point of the scoring rung: before this, the overlay's
    opinion sat next to a confidence_score it could not touch."""
    baseline, baseline_overlay = _evaluate(session, monkeypatch, None)
    disagreeing, overlay_points = _evaluate(
        session, monkeypatch,
        '{"stance": "bearish", "confidence": 78, "reasoning": "Exhausted."}',
        ai_overlay_objection_action="none",
    )

    assert baseline_overlay == 0
    assert overlay_points == -AI_OVERLAY_SCORE_CAP
    assert disagreeing < baseline


def test_an_agreeing_overlay_does_not_inflate_confidence(session, monkeypatch):
    """Agreement is free in both directions: it costs nothing and buys
    nothing. An LLM must not be able to talk the engine INTO a trade."""
    baseline, _ = _evaluate(session, monkeypatch, None)
    agreeing, overlay_points = _evaluate(
        session, monkeypatch, '{"stance": "bullish", "confidence": 95, "reasoning": "Agrees."}'
    )

    assert overlay_points == 0
    assert agreeing == baseline


def test_a_neutral_overlay_does_not_move_confidence(session, monkeypatch):
    baseline, _ = _evaluate(session, monkeypatch, None)
    neutral, overlay_points = _evaluate(
        session, monkeypatch, '{"stance": "neutral", "confidence": 40, "reasoning": "Mixed."}'
    )

    assert overlay_points == 0
    assert neutral == baseline


def test_the_confidence_rung_can_be_turned_off_on_its_own(session, monkeypatch):
    """ai_overlay_scores_confidence=False: the opinion is still produced and
    displayed, it just stops moving the number."""
    baseline, _ = _evaluate(session, monkeypatch, None)
    unscored, overlay_points = _evaluate(
        session, monkeypatch,
        '{"stance": "bearish", "confidence": 78, "reasoning": "Exhausted."}',
        ai_overlay_scores_confidence=False,
        ai_overlay_objection_action="none",
    )

    assert overlay_points == 0
    assert unscored == baseline


def test_the_overlay_penalty_is_big_enough_to_reach_the_trade_threshold(session, monkeypatch):
    """The scoring rung has to be able to change the trade/no-trade outcome
    on its own, or it is decoration with extra steps. Asserted as a property
    of the confidence scale rather than against a fixture's raw score, so it
    stays meaningful as scoring dimensions are added."""
    per_point = trade_plan_service._confidence_score(10) - trade_plan_service._confidence_score(9)
    assert per_point > 0
    # A full-cap disagreement must move confidence by a visible amount.
    assert AI_OVERLAY_SCORE_CAP * per_point >= 5


def test_the_overlay_score_is_persisted_on_a_no_trade_record(session, monkeypatch):
    """A vetoed evaluation is the case someone will most want to audit
    later, so the points the overlay cost have to survive on the row."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "bearish", "confidence": 78, "reasoning": "Exhausted."}')

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    record = session.get(TradePlanRecord, response.id)
    assert record.status == "no_trade"
    assert record.ai_overlay_score == -AI_OVERLAY_SCORE_CAP
    assert record.ai_opinion_stance == "bearish"
    assert "AI overlay disagrees" in record.signal_reasons


def test_the_overlay_is_shown_the_rule_based_confidence_not_its_own_effect(session, monkeypatch):
    """The overlay prompt gets the PRE-overlay number. Feeding it the
    post-penalty score would make the "for context only" figure partly an
    echo of the model's own previous reasoning."""
    baseline, _ = _evaluate(session, monkeypatch, None)
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(auto_execute_trade_plans=False),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "bearish", "confidence": 78, "reasoning": "Exhausted."}')

    trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert f"{baseline}" in llm.prompts[0]


def test_overlay_off_leaves_every_rung_inert(session, monkeypatch):
    """All three knobs default on, so this is the guarantee that a default
    install (master switch off) is completely unaffected by any of them."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(ai_trading_overlay_enabled=False),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "bearish", "confidence": 95, "reasoning": "n/a"}')

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.status == "executed"
    assert response.direction == "long"
    assert response.ai_overlay_score == 0
    assert session.exec(select(PaperPosition)).first() is not None


# ------------------------------------- the trade_verdict field (hedging fix)


def test_a_pass_verdict_on_a_neutral_stance_stops_the_trade(session, monkeypatch):
    """The exact failure this field was added for. A live claude_code_cli
    call returned stance `neutral` while its reasoning opened "I disagree
    with the rule-based LONG" — under stance-only logic that scored 0 and
    changed nothing, which is what "the overlay is just visual" looked like
    in practice."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(),
    )
    llm = FakeConfiguredLLMProvider(
        '{"stance": "neutral", "trade_verdict": "pass", "confidence": 72, '
        '"reasoning": "I disagree with the long — volume does not confirm it."}'
    )

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.status == "no_trade"
    assert response.ai_trade_verdict == "pass"
    assert response.ai_opinion_stance == "neutral"  # the directional read is kept as-is
    assert "would not take this trade" in response.reason
    assert session.exec(select(PaperPosition)).first() is None


def test_a_take_verdict_lets_a_contradicting_stance_through(session, monkeypatch):
    """"I read the direction differently, but the trade is still worth
    taking" must not stop anything — the model answered the trade question
    directly and said yes."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(),
    )
    llm = FakeConfiguredLLMProvider(
        '{"stance": "bearish", "trade_verdict": "take", "confidence": 60, '
        '"reasoning": "Not my directional read, but the setup is clean enough."}'
    )

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.status == "executed"
    assert response.ai_trade_verdict == "take"
    assert response.ai_overlay_score == 0
    assert session.exec(select(PaperPosition)).first() is not None


def test_a_missing_verdict_falls_back_to_the_stance(session, monkeypatch):
    """Back-compat with providers that ignore the new field: the flat
    opposite stance still stops the trade on its own."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(),
    )
    llm = FakeConfiguredLLMProvider('{"stance": "bearish", "confidence": 78, "reasoning": "Exhausted."}')

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    assert response.ai_trade_verdict is None
    assert response.status == "no_trade"
    assert "reads this bearish" in response.reason


def test_an_unrecognised_verdict_is_not_treated_as_approval(session, monkeypatch):
    """A model answering "maybe" must not be read as `take` — an
    unparseable verdict is no verdict, and the stance fallback applies."""
    llm = FakeConfiguredLLMProvider(
        '{"stance": "bearish", "trade_verdict": "maybe", "confidence": 78, "reasoning": "Unsure."}'
    )
    opinion = trade_plan_service._parse_ai_opinion(llm.response_text)
    assert opinion.trade_verdict is None
    assert opinion.stance == "bearish"
    assert trade_plan_service._overlay_contradicts_direction("long", opinion) is True


def test_the_verdict_is_persisted_and_survives_the_round_trip(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(),
    )
    llm = FakeConfiguredLLMProvider(
        '{"stance": "neutral", "trade_verdict": "pass", "confidence": 72, "reasoning": "No."}'
    )

    response = trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)

    record = session.get(TradePlanRecord, response.id)
    assert record.ai_trade_verdict == "pass"
    assert record.ai_overlay_score == -AI_OVERLAY_SCORE_CAP


# ------------------------------------------- confidence scale and the bar


def test_confidence_is_a_plain_percentage_of_the_points_earned():
    """0-100 with nothing done to it: a plan showing 56% earned 9 of the 16
    achievable points. Previously squeezed onto 20-90, which made the floor
    unreachable in both directions."""
    assert trade_plan_service._confidence_score(0) == 0
    assert trade_plan_service._confidence_score(trade_plan_service.MAX_SCORE_FOR_CONFIDENCE) == 100
    half = trade_plan_service.MAX_SCORE_FOR_CONFIDENCE / 2
    assert trade_plan_service._confidence_score(round(half)) == 50


def test_the_default_bar_preserves_the_historical_five_point_cutoff(session, monkeypatch):
    """The default moved 40 -> 30 purely because the scale changed. Both
    mean "5 of 16 points", so no setup that used to trade stops trading."""
    bar = AppSettings().min_confidence_for_trade
    assert trade_plan_service._confidence_score(4) < bar
    assert trade_plan_service._confidence_score(5) >= bar


def test_the_confidence_bar_is_configurable(session, monkeypatch):
    """Raising it must reject a setup the default accepts — the fixture
    clears the default bar by a single point."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(ai_trading_overlay_enabled=False, min_confidence_for_trade=95),
    )
    response = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )

    assert response.status == "no_trade"
    assert "needs 95%+" in response.reason


def test_lowering_the_bar_admits_a_setup_the_default_rejects(session, monkeypatch):
    """The other direction: a bar of 0 must let through anything with a
    direction, including a setup the default bar turns away."""
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(ai_trading_overlay_enabled=False, auto_execute_trade_plans=False,
                                  min_confidence_for_trade=0),
    )
    response = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )

    assert response.status == "pending"
    assert response.direction == "long"
    # And the bar is what decides it, not the trend: a bar above this
    # setup's own score rejects the identical evaluation.
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: _default_settings(ai_trading_overlay_enabled=False, auto_execute_trade_plans=False,
                                  min_confidence_for_trade=response.confidence_score + 1),
    )
    rejected = trade_plan_service.generate_trade_plan(
        "AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), NullLLMProvider(), session
    )
    assert rejected.status == "no_trade"


def test_old_settings_files_migrate_onto_the_single_objection_action():
    """A settings.json written before ai_overlay_objection_action existed
    must keep behaving the same. Without the migration pydantic silently
    drops the two retired booleans (BaseModel ignores extras), so anyone who
    had deliberately turned the veto off would find it back on after an
    upgrade — a real behaviour change applied invisibly."""
    def migrated(vetoes: bool, holds: bool) -> str:
        raw = json.dumps({"ai_overlay_vetoes_trade": vetoes, "ai_overlay_blocks_auto_execute": holds})
        return AppSettings.model_validate_json(raw).ai_overlay_objection_action

    assert migrated(True, True) == "cancel"    # cancel always won anyway
    assert migrated(True, False) == "cancel"
    assert migrated(False, True) == "hold"
    assert migrated(False, False) == "none"


def test_a_settings_file_with_neither_old_nor_new_key_takes_the_default():
    assert AppSettings.model_validate_json("{}").ai_overlay_objection_action == "cancel"


def test_an_explicit_new_key_is_never_overridden_by_the_old_ones():
    """A file carrying both (written mid-upgrade) must trust the new field."""
    raw = json.dumps({"ai_overlay_objection_action": "none", "ai_overlay_vetoes_trade": True})
    assert AppSettings.model_validate_json(raw).ai_overlay_objection_action == "none"

