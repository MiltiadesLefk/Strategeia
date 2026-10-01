"""What a price-only backtest can score, and what the confidence bar means for it."""

from __future__ import annotations

from app.analysis.scanner_scoring import score_symbol
from app.analysis.trend import ChartAnalysis
from app.backtest.coverage import TECHNICAL_VOLUME_POINT, describe_coverage, points_needed_for
from app.services.trade_plan_service import MAX_SCORE_FOR_CONFIDENCE, TECHNICAL_SCORE_CAP


def _best_possible_chart() -> ChartAnalysis:
    return ChartAnalysis(
        price=100.0, ema20=99.0, ema50=95.0, rsi14=60.0, trend="Bullish", momentum="Strong",
        pct_from_ema20=0.01, support=[95.0], resistance=[101.0],
    )


def test_the_volume_point_is_exactly_what_the_decision_moment_cannot_earn():
    chart = _best_possible_chart()
    with_volume = score_symbol("X", 100.0, 1.0, chart, volume_ratio=10.0).score
    without_volume = score_symbol("X", 100.0, 1.0, chart, volume_ratio=0.0).score
    assert with_volume == TECHNICAL_SCORE_CAP
    assert without_volume == TECHNICAL_SCORE_CAP - TECHNICAL_VOLUME_POINT


def test_price_only_coverage_numbers():
    coverage = describe_coverage(30)
    assert coverage["profile"] == "price_only"
    assert coverage["live_points_max"] == MAX_SCORE_FOR_CONFIDENCE == 16
    # technical 5 (no volume point) + weekly/SPY 2; the VIX check can only subtract
    assert coverage["achievable_points"] == 7
    assert coverage["achievable_max_confidence_pct"] == 44
    active = {part["part"]: part for part in coverage["active_parts"]}
    assert set(active) == {"technical", "market_confirmation", "vix_regime"}
    assert active["vix_regime"]["points_max"] == 0
    inactive = {part["part"] for part in coverage["inactive_parts"]}
    assert {"fundamentals", "news", "options", "insider", "earnings_surprise", "macro_event", "ai_overlay"} <= inactive


def test_the_live_bar_is_reported_as_points_of_the_achievable_maximum():
    coverage = describe_coverage(30)
    # 30% of 16 points = 5 points live; here that is 5 of the 7 that can be earned
    assert coverage["bar_points_needed"] == 5
    assert coverage["live_bar_points_needed"] == 5
    assert coverage["bar_share_of_achievable"] == 5 / 7
    assert coverage["live_bar_share_of_16"] == 5 / 16
    assert coverage["bar_reachable"] is True


def test_an_override_is_recorded_beside_the_live_bar_and_an_unreachable_bar_is_flagged():
    coverage = describe_coverage(20, live_min_confidence_for_trade=30)
    assert coverage["min_confidence_for_trade"] == 20
    assert coverage["live_min_confidence_for_trade"] == 30
    assert coverage["bar_points_needed"] == 4 and coverage["live_bar_points_needed"] == 5
    assert describe_coverage(60)["bar_reachable"] is False  # 60% needs 10 points, only 7 exist


def test_points_needed_matches_the_live_quantisation():
    assert points_needed_for(0) == 0
    assert points_needed_for(100) == 16
    assert points_needed_for(31) == 5  # 5/16 = 31.25 -> 31
    assert points_needed_for(32) == 6
