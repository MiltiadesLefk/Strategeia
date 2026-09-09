from __future__ import annotations

from app.analysis.market_confirmation import MARKET_CONFIRMATION_SCORE_CAP, score_market_confirmation
from app.analysis.trend import ChartAnalysis


def make_chart(**overrides) -> ChartAnalysis:
    defaults = dict(
        price=100.0,
        ema20=98.0,
        ema50=95.0,
        rsi14=60.0,
        trend="Bullish",
        momentum="Strong",
        pct_from_ema20=0.02,
        support=[90.0],
        resistance=[101.0],
    )
    defaults.update(overrides)
    return ChartAnalysis(**defaults)


def test_no_direction_means_no_confirmation_check_at_all():
    score, reasons = score_market_confirmation(None, make_chart(trend="Bullish"), make_chart(trend="Bullish"))
    assert score == 0
    assert reasons == []


def test_weekly_and_market_both_agree_scores_plus_two():
    score, reasons = score_market_confirmation("long", make_chart(trend="Bullish"), make_chart(trend="Bullish"))
    assert score == MARKET_CONFIRMATION_SCORE_CAP == 2
    assert len(reasons) == 2


def test_weekly_and_market_both_disagree_scores_minus_two():
    score, reasons = score_market_confirmation("long", make_chart(trend="Bearish"), make_chart(trend="Bearish"))
    assert score == -MARKET_CONFIRMATION_SCORE_CAP == -2
    assert len(reasons) == 2


def test_mixed_signals_net_to_zero():
    score, reasons = score_market_confirmation("long", make_chart(trend="Bullish"), make_chart(trend="Bearish"))
    assert score == 0
    assert len(reasons) == 2  # both still explained, just cancel out numerically


def test_neutral_chart_contributes_nothing_not_a_penalty():
    score, reasons = score_market_confirmation("long", make_chart(trend="Neutral"), make_chart(trend="Neutral"))
    assert score == 0
    assert reasons == []


def test_missing_chart_data_contributes_nothing_not_a_penalty():
    """A fetch failure (weekly_chart/market_chart is None) must never read
    as disagreement — that would silently punish a symbol for a data
    provider hiccup that has nothing to do with the actual setup."""
    score, reasons = score_market_confirmation("long", None, None)
    assert score == 0
    assert reasons == []


def test_short_direction_evaluated_symmetrically():
    score, reasons = score_market_confirmation("short", make_chart(trend="Bearish"), make_chart(trend="Bearish"))
    assert score == MARKET_CONFIRMATION_SCORE_CAP
    assert len(reasons) == 2
