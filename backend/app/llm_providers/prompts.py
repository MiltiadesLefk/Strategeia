from __future__ import annotations

from app.analysis.trend import ChartAnalysis

SYSTEM_PREAMBLE = (
    "You are a terse trading-desk analyst writing a 1-2 sentence note for a dashboard. "
    "Only narrate the numbers given below - never invent price levels, news, or facts not provided. "
    "Do not give investment advice or tell the reader what to do; describe the setup. Plain text only."
)


def build_chart_insight_prompt(symbol: str, chart: ChartAnalysis) -> str:
    levels = ""
    if chart.trend == "Bullish" and chart.resistance:
        levels = f"Nearest resistance: ${chart.resistance[0]:.2f}."
    elif chart.trend == "Bearish" and chart.support:
        levels = f"Nearest support: ${chart.support[0]:.2f}."
    return (
        f"{SYSTEM_PREAMBLE}\n\n"
        f"Symbol: {symbol}\nPrice: ${chart.price:.2f}\nTrend: {chart.trend}\nMomentum: {chart.momentum}\n"
        f"RSI(14): {chart.rsi14:.0f}\nPrice vs EMA20: {chart.pct_from_ema20 * 100:+.1f}%\n{levels}\n\n"
        "Write the chart insight note now."
    )


def build_research_summary_prompt(
    symbol: str, chart: ChartAnalysis, has_upcoming_earnings: bool, catalysts: list[str]
) -> str:
    catalysts_txt = "; ".join(catalysts) if catalysts else "none listed"
    earnings_txt = "Earnings are upcoming." if has_upcoming_earnings else "No earnings imminent."
    return (
        f"{SYSTEM_PREAMBLE}\n\n"
        f"Symbol: {symbol}\nTrend: {chart.trend}\nMomentum: {chart.momentum}\n{earnings_txt}\n"
        f"Catalysts: {catalysts_txt}\n\n"
        "Write a short fundamental/technical summary note now."
    )


def build_trade_plan_take_prompt(
    symbol: str, direction: str, entry: float, stop: float, tp1: float, rr1: float, confidence_score: int
) -> str:
    return (
        f"{SYSTEM_PREAMBLE}\n\n"
        f"Symbol: {symbol}\nDirection: {direction}\nEntry: ${entry:.2f}\nStop: ${stop:.2f}\n"
        f"Target 1: ${tp1:.2f}\nRisk:Reward: {rr1:.1f}:1\nRule-based confidence score: {confidence_score}%\n\n"
        "Write the trade plan's 'analyst take' note now."
    )
