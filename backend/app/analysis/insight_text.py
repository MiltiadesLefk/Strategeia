from __future__ import annotations

from app.analysis.trend import ChartAnalysis


def chart_insight_text(symbol: str, chart: ChartAnalysis) -> str:
    direction = "uptrend" if chart.trend == "Bullish" else "downtrend" if chart.trend == "Bearish" else "sideways range"
    pct = abs(chart.pct_from_ema20) * 100
    side = "above" if chart.pct_from_ema20 >= 0 else "below"
    momentum_txt = f"{chart.momentum.lower()} momentum"
    level_txt = ""
    if chart.trend == "Bullish" and chart.resistance:
        level_txt = f" Next resistance sits near ${chart.resistance[0]:.2f}."
    elif chart.trend == "Bearish" and chart.support:
        level_txt = f" Next support sits near ${chart.support[0]:.2f}."
    return (
        f"{symbol} is in a {chart.trend.lower()} {direction} with {momentum_txt}, "
        f"trading {pct:.1f}% {side} its EMA20 (RSI {chart.rsi14:.0f}).{level_txt}"
    )


def research_summary_text(symbol: str, chart: ChartAnalysis, has_upcoming_earnings: bool) -> str:
    outlook = "constructive" if chart.trend == "Bullish" else "cautious" if chart.trend == "Bearish" else "mixed"
    earnings_txt = " Earnings are approaching, which can add volatility." if has_upcoming_earnings else ""
    return (
        f"{symbol}'s technical picture looks {outlook} heading into the near term, "
        f"with {chart.momentum.lower()} momentum on the {chart.trend.lower()} trend.{earnings_txt}"
    )


def trade_plan_take_text(
    symbol: str, direction: str, rr1: float, confidence_score: int
) -> str:
    return (
        f"{symbol} offers a {direction} setup with a {rr1:.1f}:1 risk:reward ratio to the first target. "
        f"Rule-based confidence: {confidence_score}%. Confirm the thesis before sizing up further."
    )
