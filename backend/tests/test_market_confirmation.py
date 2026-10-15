from __future__ import annotations

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


