"""The AI Trading Overlay as a scored dimension of the confidence math.

Two invariants every test here defends:

1. The overlay can only ever SUBTRACT conviction. See
   analysis/ai_overlay_scoring.py's module docstring — the prompt anchors the
   model by showing it the rule-based verdict, so agreement is weak evidence,
   and a bonus could let an LLM push a setup the deterministic engine
   rejected over the confidence bar.
2. `trade_verdict` ("would you take this trade?") is the signal acted on,
   with the directional `stance` as the fallback. These are different
   questions and a real model conflated them in live testing.
"""

from __future__ import annotations

import pytest

from app.analysis.ai_overlay_scoring import (
    AI_OVERLAY_MODERATE_CONFIDENCE,
    AI_OVERLAY_SCORE_CAP,
    AI_OVERLAY_STRONG_CONFIDENCE,
    overlay_opposes_trade,
    score_ai_overlay,
)

STANCES = ("bullish", "bearish", "neutral", None)
VERDICTS = ("take", "pass", None)
CONFIDENCES = (None, 0, 30, 44, 45, 64, 65, 100)


# ------------------------------------------------------------ never a bonus


@pytest.mark.parametrize("direction,stance", [("long", "bullish"), ("short", "bearish")])
@pytest.mark.parametrize("confidence", [0, 50, 95, None])
def test_agreement_is_never_rewarded(direction, stance, confidence):
    """Not an oversight — the overlay prompt hands the model the rule-based
    direction for context, so agreement is partly the anchor talking. Paying
    points for it would mostly pay the engine to agree with itself."""
    assert score_ai_overlay(direction, stance, "take", confidence) == (0, [])


def test_nothing_in_the_whole_input_space_is_ever_positive():
    """Belt-and-braces sweep: no combination of direction, stance, verdict
    and confidence can produce a bonus or exceed the cap."""
    for direction in ("long", "short", None):
        for stance in STANCES:
            for verdict in VERDICTS:
                for confidence in CONFIDENCES:
                    score, _ = score_ai_overlay(direction, stance, verdict, confidence)
                    assert -AI_OVERLAY_SCORE_CAP <= score <= 0


# ------------------------------------------------- verdict beats stance


def test_a_pass_verdict_counts_even_on_a_neutral_stance():
    """The bug this field exists to fix. Live, the model answered `neutral`
    while its own reasoning opened "I disagree with the rule-based LONG" — a
    real objection worth 0 points under stance-only logic, because a neutral
    stance is (correctly) never read as opposition."""
    assert overlay_opposes_trade("long", "neutral", "pass") is True
    score, reasons = score_ai_overlay("long", "neutral", "pass", 78)
    assert score == -AI_OVERLAY_SCORE_CAP
    assert "would not take this trade" in reasons[0]


def test_a_pass_verdict_counts_even_when_the_stance_agrees():
    """"The stock probably goes up, but I would not take this particular
    entry" is a coherent, useful answer — and the verdict is the field
    answering the question that matters."""
    assert overlay_opposes_trade("long", "bullish", "pass") is True
    assert score_ai_overlay("long", "bullish", "pass", 70)[0] == -AI_OVERLAY_SCORE_CAP


def test_a_take_verdict_overrides_a_contradicting_stance():
    """"I read the direction differently but the trade is still worth
    taking" must not be scored as opposition — the model answered the trade
    question directly and said yes."""
    assert overlay_opposes_trade("long", "bearish", "take") is False
    assert score_ai_overlay("long", "bearish", "take", 90) == (0, [])


def test_a_take_verdict_is_never_a_penalty():
    for stance in STANCES:
        for confidence in CONFIDENCES:
            assert score_ai_overlay("long", stance, "take", confidence) == (0, [])


# ------------------------------- stance fallback when no verdict is given


def test_a_flat_opposite_stance_still_counts_with_no_verdict():
    """Back-compat: rows written before the verdict field existed, and
    providers that ignore it, keep working exactly as before."""
    assert overlay_opposes_trade("long", "bearish", None) is True
    assert overlay_opposes_trade("short", "bullish", None) is True
    score, reasons = score_ai_overlay("long", "bearish", None, 78)
    assert score == -AI_OVERLAY_SCORE_CAP
    assert "disagrees" in reasons[0]


@pytest.mark.parametrize("direction", ["long", "short"])
def test_a_neutral_stance_with_no_verdict_contributes_nothing(direction):
    """Unchanged from before the verdict existed: the model hedging its
    direction, with no answer to the trade question, is uncertainty rather
    than opposition. Mild hedging is most of what a real LLM returns, so
    scoring it as disagreement would tax nearly every plan."""
    assert overlay_opposes_trade(direction, "neutral", None) is False
    assert score_ai_overlay(direction, "neutral", None, 50) == (0, [])


def test_an_agreeing_stance_with_no_verdict_contributes_nothing():
    assert score_ai_overlay("long", "bullish", None, 90) == (0, [])


def test_no_direction_contributes_nothing():
    """A Neutral-trend symbol has no proposed trade to object to."""
    assert overlay_opposes_trade(None, "bearish", "pass") is False
    assert score_ai_overlay(None, "bearish", "pass", 90) == (0, [])


def test_no_stance_and_no_verdict_contributes_nothing():
    """Overlay off, provider unconfigured, or an unparseable response — all
    arrive as None and are never guessed at."""
    assert score_ai_overlay("long", None, None, None) == (0, [])


# -------------------------------------------------- penalty, by conviction


@pytest.mark.parametrize("verdict,stance", [("pass", "neutral"), (None, "bearish")])
def test_high_conviction_objection_costs_the_full_cap(verdict, stance):
    assert score_ai_overlay("long", stance, verdict, AI_OVERLAY_STRONG_CONFIDENCE)[0] == -AI_OVERLAY_SCORE_CAP


def test_penalty_is_monotonic_in_the_models_own_conviction():
    scores = [score_ai_overlay("long", "neutral", "pass", c)[0] for c in (10, 50, 90)]
    assert scores == [-1, -2, -AI_OVERLAY_SCORE_CAP]


def test_moderate_and_weak_bands_sit_where_the_prompt_says_they_do():
    """The two thresholds are lifted from the overlay prompt's own
    calibration ladder, so "strong" means what the model was told it means."""
    assert score_ai_overlay("long", "neutral", "pass", AI_OVERLAY_MODERATE_CONFIDENCE)[0] == -2
    assert score_ai_overlay("long", "neutral", "pass", AI_OVERLAY_MODERATE_CONFIDENCE - 1)[0] == -1
    assert score_ai_overlay("long", "neutral", "pass", AI_OVERLAY_STRONG_CONFIDENCE - 1)[0] == -2


def test_missing_confidence_still_counts_but_only_weakly():
    """An objection parsed without a usable number is unquantified, not
    absent: recorded, but at the lowest weight rather than assumed severe."""
    score, reasons = score_ai_overlay("long", "bearish", None, None)
    assert score == -1
    assert "conviction" not in reasons[0]  # no fabricated percentage in the text


# ------------------------------------------------------------ reason text


def test_the_reason_explains_which_signal_fired():
    """The reason lands in signal_reasons on the plan card. A "pass" on a
    neutral stance would otherwise read as an unexplained deduction."""
    _, pass_reasons = score_ai_overlay("long", "neutral", "pass", 80)
    assert pass_reasons == ["AI overlay would not take this trade at 80% conviction — against a rule-based long"]

    _, stance_reasons = score_ai_overlay("long", "bearish", None, 80)
    assert stance_reasons == ["AI overlay disagrees — reads this bearish at 80% conviction against a rule-based long"]


def test_the_reason_mentions_a_directional_read_alongside_a_pass():
    """When the model both passes AND has a directional view, say both —
    "would not take this, and reads it bearish" is more informative than
    either half."""
    _, reasons = score_ai_overlay("long", "bearish", "pass", 80)
    assert "would not take this trade" in reasons[0]
    assert "reading the stock bearish" in reasons[0]
