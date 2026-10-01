"""Ground-truth numbers for AI prompts: the snapshot, its rendering, the
overlay prompt that carries it, and the check on the figures the AI quotes."""

from __future__ import annotations

import json
import logging
from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis.ground_truth import (
    GROUND_TRUTH_BEGIN,
    GROUND_TRUTH_END,
    MAX_GROUNDING_WARNINGS,
    NOT_AVAILABLE,
    build_ground_truth,
    find_ungrounded_figures,
    render_ground_truth,
)
from app.analysis.trend import ChartAnalysis
from app.config import AppSettings
from app.data_providers.base import NewsItem, OptionsSummary
from app.llm_providers.base import LLMResult
from app.llm_providers.prompts import AI_OPINION_DATA_MARKER, build_ai_opinion_prompt
from app.portfolio.models import TradePlanRecord
from app.services import trade_plan_service
from tests.test_trade_plan_service import FakeUptrendDataProvider

TODAY = date(2026, 10, 1)


def _chart(**overrides) -> ChartAnalysis:
    values = dict(
        price=547.0, ema20=520.1234, ema50=500.0, rsi14=71.4, trend="Bullish", momentum="Strong",
        pct_from_ema20=0.0517, support=[530.0, 510.0, 490.0, 470.0], resistance=[560.55],
    )
    values.update(overrides)
    return ChartAnalysis(**values)


def _snapshot(**overrides):
    values = dict(
        quote_price=547.25, change_pct=1.234, volume_ratio=5.04, atr=12.3, week52_low=300.0, week52_high=560.0,
        earnings_date=date(2026, 10, 20), rule_based_direction="long", rule_based_confidence_pct=56,
        rule_based_points=9, rule_based_points_max=16, score_breakdown=(("technical", 5), ("news", -1)),
        today=TODAY,
    )
    values.update(overrides)
    return build_ground_truth("NVDA", _chart(), **values)


# ------------------------------------------------------------- snapshot


def test_the_snapshot_derives_only_arithmetic_on_given_figures():
    s = _snapshot()
    assert s.pct_from_ema20 == pytest.approx(5.17)
    assert s.atr_pct == pytest.approx(12.3 / 547.0 * 100)
    assert s.earnings_in_days == 19
    assert s.support == (530.0, 510.0, 490.0)  # nearest three
    assert s.resistance == (560.55,)


def test_the_options_implied_move_comes_from_the_summary():
    summary = OptionsSummary(
        symbol="NVDA", expiration="2026-10-31", put_call_volume_ratio=1.0, atm_implied_volatility=0.30
    )
    s = _snapshot(options_summary=summary)
    # 30% annualised over 30 days: 0.30 * sqrt(30/365) * 100 = 8.6%
    assert s.expected_move_pct == pytest.approx(0.30 * (30 / 365) ** 0.5 * 100, rel=0.02)


def test_a_zero_volume_ratio_is_missing_not_zero():
    s = _snapshot(volume_ratio=0.0)
    assert s.volume_ratio is None
    assert f"Volume vs 20-day average: {NOT_AVAILABLE}" in render_ground_truth(s)


def test_missing_things_render_as_not_available():
    s = build_ground_truth("BTC-USD", _chart(support=[], resistance=[]), today=TODAY)
    text = render_ground_truth(s)
    for line in (
        "Latest quote: not available",
        "Change vs previous close: not available",
        "Nearest support levels: not available",
        "Nearest resistance levels: not available",
        "ATR(14): not available",
        "52-week range: not available",
        "Options-implied move to the nearest expiration: not available",
    ):
        assert line in text
    assert "Next earnings date: not available" in text
    assert "no trade / no clear direction, confidence not available" in text
    assert "None" not in text


# ------------------------------------------------------------ rendering


def test_rendering_fixes_units_and_rounding():
    text = render_ground_truth(_snapshot())
    for line in (
        "Symbol: NVDA",
        "Price (last close of the daily chart): $547.00",
        "Latest quote: $547.25",
        "Change vs previous close: +1.2%",
        "RSI(14): 71",
        "EMA20: $520.12",
        "Price vs EMA20: +5.2%",
        "Nearest support levels: $530.00, $510.00, $490.00",
        "Nearest resistance levels: $560.55",
        "ATR(14): $12.30 (2.2% of price)",
        "Volume vs 20-day average: 5.0x",
        "52-week range: $300.00 to $560.00",
        "Next earnings date: 2026-10-20 (in 19 days)",
        "Rule-based verdict (context only): LONG, 56% confidence (9/16 points)",
        "Rule-based points by dimension: technical +5, news -1",
    ):
        assert line in text, line


def test_the_block_is_delimited_and_followed_by_the_instruction():
    text = render_ground_truth(_snapshot())
    assert text.startswith(GROUND_TRUTH_BEGIN)
    assert GROUND_TRUTH_END in text
    after = text.split(GROUND_TRUTH_END, 1)[1]
    assert "must be copied from the GROUND TRUTH block" in after
    assert "untrusted external text" in after
    assert GROUND_TRUTH_END in render_ground_truth(_snapshot(), include_instruction=False)
    assert "copied from" not in render_ground_truth(_snapshot(), include_instruction=False)


def test_rendering_is_deterministic():
    assert render_ground_truth(_snapshot()) == render_ground_truth(_snapshot())


# ------------------------------------------------------- the overlay prompt


def _prompt(**kwargs):
    return build_ai_opinion_prompt(
        "NVDA", _chart(), 5.0, None, [], [NewsItem(headline="Buyback of $5 billion announced", source="Wire", url="https://x", published_at="2026-01-01")],
        date(2026, 10, 20), "long", 56, ["keyword hit"], **kwargs,
    )


def test_the_overlay_prompt_carries_the_ground_truth_block():
    prompt = _prompt()
    data = prompt.split(AI_OPINION_DATA_MARKER, 1)[1]
    assert data.startswith(GROUND_TRUTH_BEGIN)
    assert "Price (last close of the daily chart): $547.00" in data
    assert "Buyback of $5 billion announced" in data  # headlines still follow, as data
    assert "untrusted" in prompt  # the existing headline guard is still in the preamble


def test_a_supplied_snapshot_is_the_one_rendered():
    prompt = _prompt(ground_truth=_snapshot(atr=9.99))
    assert "ATR(14): $9.99" in prompt


# ------------------------------------------------------------- claim check


def _check(text, data_text="", **snapshot_overrides):
    return find_ungrounded_figures(text, _snapshot(**snapshot_overrides), data_text)


def test_an_invented_price_is_flagged():
    assert _check("Price should revisit $412.50 soon.") == ["model quoted $412.50, not in the data given"]


def test_an_invented_decimal_percentage_is_flagged():
    assert _check("A 3.7% pullback is likely.") == ["model quoted 3.7%, not in the data given"]


@pytest.mark.parametrize(
    "text",
    [
        "Price is $547.00 with support at $530.",
        "Price is $547.1, a hair away.",  # equals the given 547.0/547.25 at the precision quoted
        "Price is $548 now.",  # within 1%
        "Resistance near $560.55.",
        "The 20-day EMA sits at $520.12.",
        "52-week high $560, low $300.",
        "ATR is $12.30, about 2.2% of price.",
        "Price is 5.2% above the 20-day EMA.",
        "Up 1.2% today.",
        "Earnings in 19 days, on 2026-10-20.",
        "The implied move is fine.",
    ],
)
def test_figures_that_are_in_the_data_are_not_flagged(text):
    assert _check(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "I am 70% confident.",
        "Roughly 50% odds.",
        "The $550 level is psychological, and $500 below it.",
        "In 2026 this changed.",
        "Volume is 5x average.",
        "RSI of 71 is stretched.",
        "Q3 2025 results.",
        "",
    ],
)
def test_round_numbers_years_and_bare_numbers_are_ignored(text):
    assert _check(text) == []


def test_a_derived_gap_between_given_levels_is_not_flagged():
    # price 547 vs EMA50 500 is +9.4%
    assert _check("Price is 9.4% above the 50-day EMA.") == []


def test_figures_from_the_rest_of_the_data_are_not_flagged():
    data = "Fundamentals: market cap $2,134,000,000,000, TTM EPS $12.34. Headline: Buyback of $5 billion announced, up 8.25%"
    assert _check("Market cap of $2.1 trillion, EPS $12.34, and a $5B buyback, shares +8.25%.", data) == []
    assert _check("Market cap of $2.1 trillion, EPS $12.34.", "") != []


def test_a_figure_quoted_twice_is_reported_once():
    assert len(_check("Maybe $412.50, or really $412.50.")) == 1


def test_warnings_are_capped():
    text = " ".join(f"${300 + i * 7}.{i}1" for i in range(12))
    assert len(find_ungrounded_figures(text, _snapshot())) == MAX_GROUNDING_WARNINGS


def test_none_text_is_fine():
    assert find_ungrounded_figures(None, _snapshot()) == []


# ----------------------------------------------------- through the service


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class LLM:
    name = "fake-llm"

    def __init__(self, reasoning, news="No headlines available."):
        self.reply = json.dumps(
            {"stance": "bearish", "trade_verdict": "pass", "confidence": 80, "reasoning": reasoning, "news_assessment": news}
        )
        self.prompts: list[str] = []

    def is_configured(self):
        return True

    def generate(self, prompt, *, max_tokens=300, temperature=0.4, tier="routine"):
        self.prompts.append(prompt)
        return LLMResult(self.reply if tier == "decision" else "a take", self.name, 1)


def _generate(session, monkeypatch, llm, **settings):
    values = dict(telegram_bot_token="", telegram_chat_id="", ai_trading_overlay_enabled=True, auto_execute_trade_plans=False)
    values.update(settings)
    monkeypatch.setattr("app.services.trade_plan_service.load_app_settings", lambda: AppSettings(**values))
    return trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)


def test_the_overlay_prompt_in_a_real_evaluation_has_the_block_with_real_numbers(session, monkeypatch):
    llm = LLM("Disagree.")
    _generate(session, monkeypatch, llm)
    prompt = next(p for p in llm.prompts if GROUND_TRUTH_BEGIN in p)
    assert "Latest quote: $547.00" in prompt
    assert "Rule-based verdict (context only): LONG" in prompt
    assert "points by dimension: technical" in prompt


def test_quoted_figures_not_in_the_data_are_stored_and_returned(session, monkeypatch, caplog):
    llm = LLM("Disagree: price will drop to $412.50 and fall 3.7% from here.")
    with caplog.at_level(logging.WARNING, logger="app.services.trade_plan_service"):
        response = _generate(session, monkeypatch, llm)

    expected = "model quoted $412.50, not in the data given\nmodel quoted 3.7%, not in the data given"
    assert response.ai_grounding_warnings == expected
    assert session.exec(select(TradePlanRecord)).first().ai_grounding_warnings == expected
    assert "$412.50" in caplog.text


def test_clean_reasoning_stores_no_warnings(session, monkeypatch):
    response = _generate(session, monkeypatch, LLM("Disagree: price is $547.00, stretched at RSI 71."))
    assert response.ai_grounding_warnings is None


def test_warnings_never_change_the_decision_or_the_confidence(session, monkeypatch):
    clean = _generate(session, monkeypatch, LLM("Disagree: the setup is stretched."))
    flagged = _generate(session, monkeypatch, LLM("Disagree: it will fall to $412.50, a 3.7% drop."))

    assert clean.ai_grounding_warnings is None and flagged.ai_grounding_warnings
    for field in (
        "status", "direction", "confidence_score", "confidence_points", "ai_overlay_score", "ai_trade_verdict",
        "entry", "stop", "tp1", "suggested_shares", "reason",
    ):
        assert getattr(clean, field) == getattr(flagged, field), field


def test_overlay_off_checks_nothing(session, monkeypatch):
    llm = LLM("price $412.50")
    response = _generate(session, monkeypatch, llm, ai_trading_overlay_enabled=False)
    assert response.ai_grounding_warnings is None
    assert not any(GROUND_TRUTH_BEGIN in p for p in llm.prompts)
