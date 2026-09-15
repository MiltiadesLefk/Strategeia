from __future__ import annotations

# The AI Trading Overlay as a scored dimension of the confidence math, in the
# same shape as every other scorer in this package (see insider_scoring /
# market_confirmation): direction-aware, named constants, hard cap, returning
# a plain (points, reasons) tuple that trade_plan_service folds in. Before
# this, the overlay's opinion was stored and displayed but read by exactly one
# line of code (the auto-execute hold), so under the default
# auto_execute_trade_plans=False it changed nothing at all.
#
# One-directional on purpose: the overlay can only ever SUBTRACT conviction,
# never add it. Same shape as score_vix_regime and score_macro_event_proximity
# (and the mirror image of score_insider_activity's buy-only asymmetry), and
# for a sharper reason than either:
#
#  - build_ai_opinion_prompt hands the model the rule-based direction and
#    confidence "for context, never as an answer key". That anchors it. An
#    agreement is therefore partly the anchor talking and is weak evidence —
#    paying points for it would mostly be paying the engine to agree with
#    itself. A disagreement reached DESPITE being shown the verdict is the
#    informative case, and it is the one that gets paid.
#  - A bonus could push a setup the rule-based engine scored under
#    MIN_CONFIDENCE_FOR_TRADE over the line, which is the LLM originating a
#    signal — the one thing CLAUDE.md's "analysis math is deliberately
#    non-ML" boundary exists to prevent. Subtracting has no such failure mode:
#    the worst an overlay can do is talk the engine out of a trade.
#
# The signal the penalty keys off is `trade_verdict` ("would you take this
# trade?") when the model gives one, falling back to a flat-opposite stance
# otherwise — see overlay_opposes_trade for why asking the trade question
# directly matters.
#
# Being one-directional, this cap is deliberately NOT part of
# trade_plan_service.MAX_SCORE_FOR_CONFIDENCE — it can never contribute to the
# achievable maximum, so including it would deflate every confidence score in
# the app (see that constant's own comment). Sized at 3 to match
# FUNDAMENTAL_SCORE_CAP: enough that a high-conviction contradiction can pull
# a marginal plan under the trade threshold on its own, not enough to sink a
# setup that everything else agrees on.
AI_OVERLAY_SCORE_CAP = 3

# Bands on the model's own `confidence` field — its stated conviction in its
# OWN stance, on the 0-100 scale build_ai_opinion_prompt defines for it (not
# the rule engine's own points-earned percentage; the prompt says so).
# Both numbers are lifted verbatim from that prompt's calibration ladder so
# "strong" here means what the model was told it means: 65+ is "multiple
# independent signals line up cleanly with no significant contradiction",
# 45+ is "a reasonably solid, ordinary setup", and below 45 is the model's
# own "conviction is genuinely weak" band. Keeping the two in sync matters —
# if that ladder is ever reworded, these move with it.
AI_OVERLAY_STRONG_CONFIDENCE = 65
AI_OVERLAY_MODERATE_CONFIDENCE = 45


def overlay_opposes_trade(
    direction: str | None,
    stance: str | None,
    trade_verdict: str | None,
) -> bool:
    """True when the overlay is against taking THIS trade. Two ways to be:

    1. `trade_verdict == "pass"` — the model's direct answer to "would you
       take this trade?". This is the authoritative signal when present.
    2. A stance that is the flat opposite of the direction (long vs
       "bearish", short vs "bullish") — the fallback for a response with no
       usable verdict, and what the whole feature ran on before the verdict
       field existed, so old rows and providers that ignore the field keep
       working.

    The verdict was added because stance alone silently lost real
    objections. `stance` answers "where does the stock go?" while the
    decision needs "should we take this?", and they come apart constantly.
    Observed live: the model returned `neutral` while its own reasoning
    opened "I disagree with the rule-based LONG" — a genuine objection that
    scored exactly 0 under stance-only logic, because a neutral stance is
    (correctly) never read as opposition. Asking the trade question directly
    is what fixes that; tightening the stance wording would only have
    pushed the model into claiming a bearish view it did not hold.

    A `take` verdict is never opposition even alongside a contradicting
    stance — the model saying "I read this differently but the trade is
    still worth taking" is a legitimate, useful answer, and the verdict is
    the field that answers the question being asked.
    """
    if direction is None:
        return False
    if trade_verdict == "pass":
        return True
    if trade_verdict == "take":
        return False
    return (direction == "long" and stance == "bearish") or (
        direction == "short" and stance == "bullish"
    )


def score_ai_overlay(
    direction: str | None,
    stance: str | None,
    trade_verdict: str | None,
    ai_confidence: int | None,
) -> tuple[int, list[str]]:
    """Penalty (0 to -AI_OVERLAY_SCORE_CAP) when the overlay is against the
    trade (see overlay_opposes_trade), scaled by how sure the model says it
    is. 0 in every other case.

    Never positive — see the module docstring above for why agreement is not
    paid. Contributes 0, never a guess, whenever there is nothing real to
    score: overlay off, provider unconfigured, an unparseable response, or a
    Neutral-trend symbol with no direction to object to.

    A missing `ai_confidence` (the model objected but returned no usable
    number) is treated as the weakest band rather than dropped: an
    unquantified objection is still an objection, but it does not get to
    swing the decision on its own.
    """
    if not overlay_opposes_trade(direction, stance, trade_verdict):
        return 0, []

    if ai_confidence is None or ai_confidence < AI_OVERLAY_MODERATE_CONFIDENCE:
        penalty = 1
    elif ai_confidence < AI_OVERLAY_STRONG_CONFIDENCE:
        penalty = 2
    else:
        penalty = AI_OVERLAY_SCORE_CAP

    confidence_note = f" at {ai_confidence}% conviction" if ai_confidence is not None else ""
    # Report whichever signal actually fired, so the plan card explains the
    # penalty rather than just showing it — a "pass" verdict on a neutral
    # stance would otherwise read as an unexplained deduction.
    if trade_verdict == "pass":
        stance_note = f", reading the stock {stance}" if stance and stance != "neutral" else ""
        reason = (
            f"AI overlay would not take this trade{confidence_note}{stance_note} "
            f"— against a rule-based {direction}"
        )
    else:
        reason = (
            f"AI overlay disagrees — reads this {stance}{confidence_note} "
            f"against a rule-based {direction}"
        )
    return -min(penalty, AI_OVERLAY_SCORE_CAP), [reason]
