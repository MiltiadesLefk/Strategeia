"""Which parts of the score a backtest can honestly rebuild, and what the
confidence bar means for them.

A live plan can earn up to MAX_SCORE_FOR_CONFIDENCE (16) points. A price-only
backtest only has prices, so only the parts built from prices can score; the rest
contribute 0 because their data is unavailable (see data_provider.py). A run may
switch on the dated parts that CAN be rebuilt for a past date (fundamentals,
insider buying, the earnings-surprise record); each switched-on part moves from
"not in this run" to "scored" and raises the reachable points by what it can
actually add. That makes
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
from app.analysis.live_evidence import EXTRA_CAPS, EXTRA_LABELS, EXTRA_PARTS, PRICE_ONLY_PARTS
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
PROFILE_PRICE_PLUS_DATED = "price_plus_dated_data"

# What each switched-on part can add. Fundamentals: +1 for revenue growth and +1 for
# sitting near the 52-week high (a near-low position is a separate branch of the
# same rule, so the two price-range cases never add up); the third live point is the
# earnings-date penalty, which is a deduction and is not rebuilt (see below).
# A test (test_backtest_dated_data.py) checks these numbers against the real scorers.
FUNDAMENTALS_REACHABLE_POINTS = 2


# The switchable parts and how a summary sentence names them.
OPTIONAL_PART_NAMES = {
    "fundamentals": "fundamentals",
    "insider": "insider buying",
    "earnings_surprise": "the earnings surprise record",
}


def points_needed_for(min_confidence_for_trade: int) -> int | None:
    """Fewest evidence points whose live confidence percentage reaches the bar, or
    None when even the maximum cannot."""
    for points in range(MAX_SCORE_FOR_CONFIDENCE + 1):
        if _confidence_score(points) >= min_confidence_for_trade:
            return points
    return None


def describe_coverage(
    min_confidence_for_trade: int,
    live_min_confidence_for_trade: int | None = None,
    *,
    include_fundamentals: bool = False,
    include_insiders: bool = False,
    include_earnings: bool = False,
) -> dict[str, Any]:
    """The coverage record stored with a run (JSON-able). The `include_*` flags are
    the run's switches for the dated parts (see params.BacktestParams)."""
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
    # Relative strength and the volume trend come from the same bars the chart uses, so a backtest scores them.
    for key in PRICE_ONLY_PARTS:
        active.append(
            {
                "part": key,
                "label": {"relative_strength": "Return against the market (SPY)", "volume_trend": "Accumulation or distribution over 20 days"}[key],
                "points_max": EXTRA_CAPS[key],
                "live_points_max": EXTRA_CAPS[key],
                "note": "from prices and volume alone",
            }
        )
    optional_parts = [
        (
            include_fundamentals, "fundamentals", "Fundamentals: revenue growth and nearness to the 52-week high or low",
            FUNDAMENTALS_REACHABLE_POINTS, FUNDAMENTAL_SCORE_CAP,
            "revenue by SEC filing date; the 52-week range from prices; market cap, P/E and the earnings-date penalty are not rebuilt",
            "no dated company data in a price history (switch it on for this run)",
        ),
        (
            include_insiders, "insider", "Insider buying (Form 4)", INSIDER_SCORE_CAP, INSIDER_SCORE_CAP,
            "dated by SEC acceptance time; a symbol whose filings were never downloaded scores 0",
            "dated insider filings are available but were not switched on for this run",
        ),
        (
            include_earnings, "earnings_surprise", "Earnings surprise record", SURPRISE_TRACK_RECORD_CAP, SURPRISE_TRACK_RECORD_CAP,
            "dated by report day (public from the end of that day); needs 4 reported quarters",
            "dated earnings history is available but was not switched on for this run",
        ),
    ]
    for enabled, part, label, points_max, live_max, note, _reason in optional_parts:
        if enabled:
            active.append(
                {"part": part, "label": label, "points_max": points_max, "live_points_max": live_max, "note": note}
            )
    inactive = [
        (part, label, cap, reason)
        for enabled, part, label, _points, cap, _note, reason in optional_parts
        if not enabled
    ]
    inactive += [
        ("news", "News sentiment", NEWS_SCORE_CAP, "no free dated news history"),
        ("options", "Options positioning", OPTIONS_SCORE_CAP, "no free options history"),
        (
            "earnings_date", "Earnings-date proximity (penalty inside the fundamentals score)", 1,
            "no source says when the next report date was announced, so it is never used (a backtest trades into reports a little more freely than the live app)",
        ),
        ("expected_move", "Options-implied expected move (penalty)", EXPECTED_MOVE_SCORE_CAP, "no options history"),
        ("macro_event", "Macro-event proximity (penalty)", MACRO_EVENT_SCORE_CAP, "the event calendar covers 2026 only, so it is left out"),
        ("ai_overlay", "AI trading overlay (penalty)", AI_OVERLAY_SCORE_CAP, "an AI that already knows what happened cannot be tested honestly"),
    ]
    not_rebuilt = {
        "congress": "the Smart Money archive is not rebuilt for a past date",
        "funds": "the Smart Money archive is not rebuilt for a past date",
        "ownership": "the Smart Money archive is not rebuilt for a past date",
        "short_volume": "no short-volume history is loaded for a backtest",
        "filing_8k": "no 8-K archive is loaded for a backtest",
        "financial_health": "today's company figures are not rebuilt for a past date",
        "valuation": "today's P/E, DCF and peers are not rebuilt for a past date",
        "analyst_consensus": "no consensus history is available for a past date",
        "options_oi": "no options history is available for a past date",
        "fed_window": "the Fed archive is not loaded for a backtest",
        "post_mentions": "the posts archive is not loaded for a backtest",
    }
    inactive += [
        (key, EXTRA_LABELS[key], EXTRA_CAPS[key], reason) for key, reason in not_rebuilt.items()
    ]
    achievable = sum(part["points_max"] for part in active)
    live_bar_points = points_needed_for(live_min_confidence_for_trade if live_min_confidence_for_trade is not None else min_confidence_for_trade)
    bar_points = points_needed_for(min_confidence_for_trade)
    switched_on = [OPTIONAL_PART_NAMES[part["part"]] for part in active if part["part"] in OPTIONAL_PART_NAMES]
    if switched_on:
        summary = (
            f"Prices plus dated data ({', '.join(switched_on)}): {achievable} of the {MAX_SCORE_FOR_CONFIDENCE} "
            "live points can be earned. News, options, the earnings date, macro events and the AI contribute 0"
            + ("" if all(p in {x["part"] for x in active} for p in OPTIONAL_PART_NAMES) else ", as do the dated parts left switched off")
            + "."
        )
    else:
        summary = (
            f"Price-only: {achievable} of the {MAX_SCORE_FOR_CONFIDENCE} live points can be earned. "
            "News, options, fundamentals, insiders, earnings history, macro events and the AI all contribute 0."
        )
    return {
        "profile": PROFILE_PRICE_PLUS_DATED if switched_on else PROFILE_PRICE_ONLY,
        "included": {
            "fundamentals": include_fundamentals, "insiders": include_insiders, "earnings": include_earnings,
        },
        "summary": summary,
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
