"""Which parts of the score a backtest can honestly rebuild, and what the
confidence bar means for them.

A live plan can earn up to MAX_SCORE_FOR_CONFIDENCE (16) points. A price-only
backtest only has prices, so only the parts built from prices can score; the rest
contribute 0 because their data is unavailable (see data_provider.py). That makes
the confidence percentage on a backtested plan a share of 16 points of which only
some are reachable, and the live bar (`min_confidence_for_trade`, 30% = 5 points)
is therefore a much higher bar here than it is live. This module spells that out
and records it with every run. It never changes the bar: an optional override is
a run parameter, recorded next to these numbers.
"""

from __future__ import annotations

from typing import Any

from app.analysis.ai_overlay_scoring import AI_OVERLAY_SCORE_CAP
from app.analysis.earnings_history_scoring import SURPRISE_TRACK_RECORD_CAP
from app.analysis.expected_move import EXPECTED_MOVE_SCORE_CAP
from app.analysis.fundamental_scoring import FUNDAMENTAL_SCORE_CAP, NEWS_SCORE_CAP
from app.analysis.insider_scoring import INSIDER_SCORE_CAP
from app.analysis.macro_calendar import MACRO_EVENT_SCORE_CAP
from app.analysis.market_confirmation import MARKET_CONFIRMATION_SCORE_CAP, VIX_REGIME_SCORE_CAP
from app.analysis.options_scoring import OPTIONS_SCORE_CAP
from app.services.trade_plan_service import (
    MAX_SCORE_FOR_CONFIDENCE,
    TECHNICAL_SCORE_CAP,
    _confidence_score,
)

# The scanner score's volume point ("volume above 1.5x its 20-day average") needs
# the day's volume, which does not exist yet at the 09:45 decision moment: the
# backtest quote reports 0 volume then (data_provider.py), so that one point is
# unreachable and the technical part tops out one point below its live cap. A
# test (test_backtest_coverage.py) checks this number against the real scorer.
TECHNICAL_VOLUME_POINT = 1

PROFILE_PRICE_ONLY = "price_only"


def points_needed_for(min_confidence_for_trade: int) -> int | None:
    """Fewest evidence points whose live confidence percentage reaches the bar, or
    None when even the maximum cannot."""
    for points in range(MAX_SCORE_FOR_CONFIDENCE + 1):
        if _confidence_score(points) >= min_confidence_for_trade:
            return points
    return None


def describe_coverage(min_confidence_for_trade: int, live_min_confidence_for_trade: int | None = None) -> dict[str, Any]:
    """The coverage record stored with a run (JSON-able)."""
    active = [
        {
            "part": "technical",
            "label": "Daily chart (trend, momentum, nearness to a key level, RSI)",
            "points_max": TECHNICAL_SCORE_CAP - TECHNICAL_VOLUME_POINT,
            "live_points_max": TECHNICAL_SCORE_CAP,
            "note": "the volume-spike point cannot be earned at the 09:45 decision moment",
        },
        {
            "part": "market_confirmation",
            "label": "Weekly chart and SPY agreeing with the direction",
            "points_max": MARKET_CONFIRMATION_SCORE_CAP,
            "live_points_max": MARKET_CONFIRMATION_SCORE_CAP,
            "note": None,
        },
        {
            "part": "vix_regime",
            "label": "VIX regime (a penalty only, never a bonus)",
            "points_max": 0,
            "live_points_max": 0,
            "penalty_down_to": -VIX_REGIME_SCORE_CAP,
            "note": "can only subtract",
        },
    ]
    inactive = [
        ("fundamentals", "Fundamentals", FUNDAMENTAL_SCORE_CAP, "no dated company data in a price history"),
        ("news", "News sentiment", NEWS_SCORE_CAP, "no free dated news history"),
        ("options", "Options positioning", OPTIONS_SCORE_CAP, "no free options history"),
        ("insider", "Insider buying", INSIDER_SCORE_CAP, "dated insider filings are not plugged in yet"),
        ("earnings_surprise", "Earnings surprise record", SURPRISE_TRACK_RECORD_CAP, "dated earnings history is not plugged in yet"),
        ("expected_move", "Options-implied expected move (penalty)", EXPECTED_MOVE_SCORE_CAP, "no options history"),
        ("macro_event", "Macro-event proximity (penalty)", MACRO_EVENT_SCORE_CAP, "the event calendar covers 2026 only, so it is left out"),
        ("ai_overlay", "AI trading overlay (penalty)", AI_OVERLAY_SCORE_CAP, "an AI that already knows what happened cannot be tested honestly"),
    ]
    achievable = sum(part["points_max"] for part in active)
    live_bar_points = points_needed_for(live_min_confidence_for_trade if live_min_confidence_for_trade is not None else min_confidence_for_trade)
    bar_points = points_needed_for(min_confidence_for_trade)
    return {
        "profile": PROFILE_PRICE_ONLY,
        "summary": (
            f"Price-only: {achievable} of the {MAX_SCORE_FOR_CONFIDENCE} live points can be earned. "
            "News, options, fundamentals, insiders, earnings history, macro events and the AI all contribute 0."
        ),
        "live_points_max": MAX_SCORE_FOR_CONFIDENCE,
        "achievable_points": achievable,
        "achievable_max_confidence_pct": _confidence_score(achievable),
        "active_parts": active,
        "inactive_parts": [
            {"part": part, "label": label, "live_points_max": cap, "reason": reason}
            for part, label, cap, reason in inactive
        ],
        "min_confidence_for_trade": min_confidence_for_trade,
        "live_min_confidence_for_trade": live_min_confidence_for_trade,
        "bar_points_needed": bar_points,
        "bar_share_of_achievable": (bar_points / achievable) if bar_points is not None and achievable else None,
        "live_bar_points_needed": live_bar_points,
        "live_bar_share_of_16": (live_bar_points / MAX_SCORE_FOR_CONFIDENCE) if live_bar_points is not None else None,
        "bar_reachable": bar_points is not None and bar_points <= achievable,
    }
