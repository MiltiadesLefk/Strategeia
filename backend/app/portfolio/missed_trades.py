"""The missed-trades ledger, read side: classify the plans that were not executed.

Hold mode writes no declined plans, so nothing computes new counterfactual
outcomes any more (the simulator, the POST and the daily job are gone). What stays:
`classify_plan` and the category constants, which the report
(missed_trade_report.py) and the smart-money review still use,
and the stored `MissedTradeOutcome` rows, which keep displaying the old results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.portfolio.models import TradePlanRecord

# ----------------------------------------------------------------- categories

CATEGORY_LOW_CONFIDENCE = "low_confidence"  # no_trade: the evidence score was under the bar
CATEGORY_AI_VETO = "ai_veto"  # no_trade: the AI Trading Overlay objected ("cancel")
CATEGORY_NEUTRAL_TREND = "neutral_trend"  # no_trade: no trend, so no direction to simulate
CATEGORY_HELD_BY_AI = "held_by_ai"  # a plan was written and left pending by the overlay ("hold")
CATEGORY_NOT_EXECUTED = "not_executed"  # a plan was written and never filled (see `detail`)
CATEGORY_UNCLASSIFIED = "unclassified"  # a non-executed plan whose text matches none of the above

CATEGORIES: tuple[str, ...] = (
    CATEGORY_AI_VETO,
    CATEGORY_LOW_CONFIDENCE,
    CATEGORY_HELD_BY_AI,
    CATEGORY_NOT_EXECUTED,
    CATEGORY_NEUTRAL_TREND,
    CATEGORY_UNCLASSIFIED,
)

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_AI_VETO: "AI veto",
    CATEGORY_LOW_CONFIDENCE: "Under the confidence bar",
    CATEGORY_HELD_BY_AI: "Held by the AI",
    CATEGORY_NOT_EXECUTED: "Written, never filled",
    CATEGORY_NEUTRAL_TREND: "No trend",
    CATEGORY_UNCLASSIFIED: "Other",
}

# `detail` for CATEGORY_NOT_EXECUTED: why the engine did not fill the plan.
DETAIL_INSUFFICIENT_CASH = "insufficient_cash"
DETAIL_POSITION_CAP = "position_cap"
DETAIL_SECTOR_CAP = "sector_cap"
DETAIL_STALE_PRICE = "stale_price"
DETAIL_DUPLICATE_POSITION = "duplicate_position"
DETAIL_MARKET_CLOSED_REDO = "market_closed_awaiting_redo"
DETAIL_AWAITING_MANUAL = "awaiting_manual"
DETAIL_OTHER_REFUSAL = "other_refusal"

DETAIL_LABELS: dict[str, str] = {
    DETAIL_INSUFFICIENT_CASH: "Not enough cash for one share",
    DETAIL_POSITION_CAP: "At the position cap",
    DETAIL_SECTOR_CAP: "At the sector cap",
    DETAIL_STALE_PRICE: "Price had moved too far from the plan",
    DETAIL_DUPLICATE_POSITION: "Symbol already held",
    DETAIL_MARKET_CLOSED_REDO: "Market closed: waiting for the redo at the open",
    DETAIL_AWAITING_MANUAL: "Pending: auto-execute not attempted",
    DETAIL_OTHER_REFUSAL: "Refused by the engine",
}

# The exact beginnings of the strings trade_plan_service / automation_service write.
# Matched by prefix (and for the engine's refusals, a stable phrase of the
# exception text) in ONE place so a reworded message shows up as a failing test
# (tests/test_missed_trades.py runs the real generator) rather than as plans
# silently drifting into "unclassified".
REASON_NO_TREND_PREFIX = "No clear trend"
REASON_AI_VETO_PREFIX = "AI Trading Overlay"
REASON_LOW_CONFIDENCE_PREFIX = "Confidence too low"
NOTE_HELD_BY_AI_PREFIX = "Auto-execute held: AI Trading Overlay"
NOTE_REFUSED_PREFIX = "Auto-execute skipped:"
NOTE_MARKET_CLOSED_PREFIX = "Market closed ("

# Phrases inside the engine's refusal messages (portfolio/engine.py), in the order
# they are tested.
_REFUSAL_PHRASES: tuple[tuple[str, str], ...] = (
    ("already has an open position", DETAIL_DUPLICATE_POSITION),
    ("-position cap", DETAIL_POSITION_CAP),
    ("-per-sector cap", DETAIL_SECTOR_CAP),
    ("can't afford 1 share", DETAIL_INSUFFICIENT_CASH),
    ("past the", DETAIL_STALE_PRICE),
)

_DIRECTION_IN_VETO = re.compile(r"rule-based (long|short)")
_TREND_IN_LOW_CONFIDENCE = re.compile(r"despite a (bullish|bearish) trend")


@dataclass(frozen=True)
class MissedClassification:
    category: str
    detail: str | None
    # Whether a hypothetical trade can and should be simulated for this plan.
    simulate: bool
    label: str


def classify_plan(plan: TradePlanRecord) -> MissedClassification | None:
    """Which kind of missed trade `plan` is, or None when it is not one: executed
    plans are the real record, and a discarded plan was superseded by a newer one
    (a regenerated plan, or the fresh plan an off-hours redo made), which is the
    decision that counts. Matches the strings the live code writes, nothing looser."""
    if plan.status == "no_trade":
        reason = (plan.reason or "").strip()
        if reason.startswith(REASON_NO_TREND_PREFIX):
            return MissedClassification(CATEGORY_NEUTRAL_TREND, None, False, CATEGORY_LABELS[CATEGORY_NEUTRAL_TREND])
        if reason.startswith(REASON_AI_VETO_PREFIX):
            return MissedClassification(CATEGORY_AI_VETO, None, True, CATEGORY_LABELS[CATEGORY_AI_VETO])
        if reason.startswith(REASON_LOW_CONFIDENCE_PREFIX):
            return MissedClassification(CATEGORY_LOW_CONFIDENCE, None, True, CATEGORY_LABELS[CATEGORY_LOW_CONFIDENCE])
        return MissedClassification(CATEGORY_UNCLASSIFIED, None, False, CATEGORY_LABELS[CATEGORY_UNCLASSIFIED])

    if plan.status == "pending":
        note = (plan.auto_execute_note or "").strip()
        if note.startswith(NOTE_HELD_BY_AI_PREFIX):
            return MissedClassification(CATEGORY_HELD_BY_AI, None, True, CATEGORY_LABELS[CATEGORY_HELD_BY_AI])
        if note.startswith(NOTE_MARKET_CLOSED_PREFIX):
            # Waiting for its redo: the fresh plan the redo makes is the decision, and this
            # one is retired when it does, so simulating both would count one idea twice.
            return _not_executed(DETAIL_MARKET_CLOSED_REDO, simulate=False)
        if note.startswith(NOTE_REFUSED_PREFIX):
            for phrase, detail in _REFUSAL_PHRASES:
                if phrase in note:
                    # A duplicate is a second plan for something already held: the position
                    # exists, so simulating it would count that one idea twice.
                    return _not_executed(detail, simulate=detail != DETAIL_DUPLICATE_POSITION)
            return _not_executed(DETAIL_OTHER_REFUSAL, simulate=True)
        if not note:
            # No auto-execute attempt was made (the setting is off, or the position cap
            # stopped the auto-scan before it tried). Still a plan nobody acted on.
            return _not_executed(DETAIL_AWAITING_MANUAL, simulate=True)
        return MissedClassification(CATEGORY_UNCLASSIFIED, None, False, CATEGORY_LABELS[CATEGORY_UNCLASSIFIED])
    return None


def _not_executed(detail: str, *, simulate: bool) -> MissedClassification:
    return MissedClassification(CATEGORY_NOT_EXECUTED, detail, simulate, DETAIL_LABELS[detail])


def _direction_from_text(plan: TradePlanRecord) -> str | None:
    """A no-trade decision stores no direction, but the reason names it: the veto
    says "against a rule-based long", the confidence bar "despite a bullish trend".
    That is the direction the live evaluation had, so it beats re-deriving it from
    a chart that may differ by a partial bar."""
    reason = plan.reason or ""
    match = _DIRECTION_IN_VETO.search(reason)
    if match:
        return match.group(1)
    match = _TREND_IN_LOW_CONFIDENCE.search(reason)
    if match:
        return "long" if match.group(1) == "bullish" else "short"
    return None
