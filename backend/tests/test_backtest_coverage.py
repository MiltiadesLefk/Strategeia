"""What a price-only backtest can score, and what the confidence bar means for it."""

from __future__ import annotations

from app.analysis.trend import ChartAnalysis


def _best_possible_chart() -> ChartAnalysis:
    return ChartAnalysis(
        price=100.0, ema20=99.0, ema50=95.0, rsi14=60.0, trend="Bullish", momentum="Strong",
        pct_from_ema20=0.01, support=[95.0], resistance=[101.0],
    )


