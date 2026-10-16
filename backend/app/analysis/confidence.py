"""The confidence arithmetic and the ATR-floored stop, shared by the live plan
builder, the backtester and the historical calibration reports. Lived in
services/trade_plan_service.py (which re-exports it for now)."""

from __future__ import annotations

from app.analysis.earnings_history_scoring import SURPRISE_TRACK_RECORD_CAP
from app.analysis.fundamental_scoring import FUNDAMENTAL_SCORE_CAP, NEWS_SCORE_CAP
from app.analysis.insider_scoring import INSIDER_SCORE_CAP
from app.analysis.live_evidence import EXTRA_MAX_POINTS
from app.analysis.market_confirmation import MARKET_CONFIRMATION_SCORE_CAP
from app.analysis.options_scoring import OPTIONS_SCORE_CAP

STOP_BUFFER_PCT = 0.01
FALLBACK_STOP_PCT = 0.03

# Volatility floor on the stop. A support level 1% under price is a perfectly
# good stop on a name that moves 0.8% a day and pure noise on one that moves
# 5% — the level-derived stop says nothing about whether it sits inside the
# instrument's normal daily range. Below this multiple of ATR the stop is
# pushed out to it, so "stopped out" means the setup actually broke rather
# than that the stock breathed. 1.5x is the conventional floor for a swing
# stop and keeps the same named-constant, non-ML shape as the rest of
# analysis/.
ATR_PERIOD = 14
ATR_STOP_MULTIPLE = 1.5
TECHNICAL_SCORE_CAP = 6
# Deliberately excludes VIX_REGIME_SCORE_CAP, EXPECTED_MOVE_SCORE_CAP,
# MACRO_EVENT_SCORE_CAP and AI_OVERLAY_SCORE_CAP: all four are
# one-directional (only ever 0 or a penalty, never a bonus — see each
# docstring), so none can contribute to the achievable maximum. Including
# any of them here would inflate the denominator and deflate every
# confidence score even when the corresponding risk is calm/absent and
# contributing nothing. SURPRISE_TRACK_RECORD_CAP is
# included: like fundamental_score/insider_score it's a genuine +/- swing, so
# it belongs in the achievable max the same way they do.
MAX_SCORE_FOR_CONFIDENCE = (
    TECHNICAL_SCORE_CAP
    + FUNDAMENTAL_SCORE_CAP
    + NEWS_SCORE_CAP
    + MARKET_CONFIRMATION_SCORE_CAP
    + OPTIONS_SCORE_CAP
    + INSIDER_SCORE_CAP
    + SURPRISE_TRACK_RECORD_CAP
    + EXTRA_MAX_POINTS
)
# A straight 0-100 percentage of the achievable evidence: a plan showing 56%
# earned 9 of the 16 points this engine can award, and nothing else is being
# done to the number. It used to be squeezed onto a 20-90 range, which made
# the floor unreachable in both directions and every score harder to reason
# about — a "20%" plan was not weakly-evidenced, it was the worst possible,
# and no plan could ever read below it however bad the evidence was. The
# label stays "Confidence", never "Win Probability" (see notes/Decisions.md):
# it measures how much of the evidence lined up, not the odds of the trade
# working.
CONFIDENCE_FLOOR = 0
CONFIDENCE_CEILING = 100

# "We don't target more trades, but better trades": a symbol clearing the
# trend check still doesn't get a plan unless the combined technical +
# fundamental + news confidence clears the configured bar
# (AppSettings.min_confidence_for_trade). Below it (or on a Neutral trend),
# generate_trade_plan returns/persists an explicit no-trade decision instead
# of forcing a mediocre plan out the door, and deliberately skips the plan
# LLM call in that path too — a rejected symbol doesn't need (and shouldn't
# spend tokens on) an AI narrative, only a rule-based reason.


def _derive_entry_and_stop(
    direction: str,
    price: float,
    support: list[float],
    resistance: list[float],
    atr_value: float | None = None,
) -> tuple[float, float]:
    """Structure first, volatility as a floor: the stop goes just past the
    nearest level, then gets pushed out if that would put it closer than
    ATR_STOP_MULTIPLE x ATR. Never pulled IN — a level further away than the
    ATR floor is respected as-is, since structure is the better reason. A
    missing ATR read (too few bars) just skips the floor."""
    entry = price
    if direction == "long":
        stop = support[0] * (1 - STOP_BUFFER_PCT) if support else entry * (1 - FALLBACK_STOP_PCT)
        if atr_value:
            stop = min(stop, entry - ATR_STOP_MULTIPLE * atr_value)
    else:
        stop = resistance[0] * (1 + STOP_BUFFER_PCT) if resistance else entry * (1 + FALLBACK_STOP_PCT)
        if atr_value:
            stop = max(stop, entry + ATR_STOP_MULTIPLE * atr_value)
    return entry, stop


def clamp_points(total: int) -> int:
    """The raw evidence points behind a confidence percentage, clamped to
    the range the percentage can express. Surfaced on the response because
    confidence is quantised, not continuous: with MAX_SCORE_FOR_CONFIDENCE
    at 16 there are only 17 reachable values, 6.25 points apart, so a bare
    "44%" implies a precision the engine does not have. Showing "7 / 16"
    beside it is the honest version, and matches this codebase's habit of
    displaying the number a verdict came from rather than just the verdict.
    """
    return max(0, min(MAX_SCORE_FOR_CONFIDENCE, total))


def _confidence_score(score: int) -> int:
    clamped = clamp_points(score)
    span = CONFIDENCE_CEILING - CONFIDENCE_FLOOR
    return round(CONFIDENCE_FLOOR + (clamped / MAX_SCORE_FOR_CONFIDENCE) * span)
