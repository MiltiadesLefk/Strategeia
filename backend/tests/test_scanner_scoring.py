from __future__ import annotations

from app.analysis.scanner_scoring import score_symbol
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


def test_strong_bullish_setup_scores_potential_setup():
    chart = make_chart(trend="Bullish", momentum="Strong", resistance=[100.5])
    result = score_symbol("AAPL", 100.0, 2.0, chart, volume_ratio=2.0)
    assert result.direction == "long"
    assert result.signal == "potential_setup"
    assert result.score >= 4


def test_neutral_trend_scores_no_signal():
    chart = make_chart(trend="Neutral", momentum="Weak")
    result = score_symbol("MSFT", 100.0, 0.1, chart, volume_ratio=1.0)
    assert result.direction is None
    assert result.signal == "no_signal"
    assert result.score == 0


def test_weak_bullish_with_low_volume_scores_watching():
    chart = make_chart(trend="Bullish", momentum="Weak", resistance=[150.0])
    result = score_symbol("TSLA", 100.0, 0.5, chart, volume_ratio=1.0)
    assert result.signal == "watching"
    assert result.score == 2


def test_overextended_rsi_penalizes_score():
    chart = make_chart(trend="Bullish", momentum="Strong", rsi14=80.0, resistance=[150.0])
    result = score_symbol("NVDA", 100.0, 3.0, chart, volume_ratio=1.0)
    # 2 (direction) + 2 (strong momentum) - 1 (overextended) = 3
    assert result.score == 3
    assert result.signal == "watching"
