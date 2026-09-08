from __future__ import annotations

from app.analysis.trend import ChartAnalysis

# Shared ground rules every persona below inherits verbatim. These encode the
# hard constraint from CLAUDE.md's "Analysis math is deliberately non-ML"
# section: the rule-based analysis/risk modules already decided the signal —
# the LLM's only job is to narrate the numbers it is handed. If a bug report
# ever says "the AI got the signal wrong", the bug is in analysis/trend.py or
# risk/position_sizing.py, not in one of these prompts.
COMMON_RULES = (
    "Ground rules, all mandatory:\n"
    "- Use ONLY the figures listed below. Never invent, estimate, or imply a price level, "
    "date, news event, or fact that is not explicitly given.\n"
    "- Never recompute or contradict the trend/direction/levels given — they come from a "
    "separate rule-based engine and are not yours to second-guess.\n"
    "- Never give investment advice or tell the reader what to do (no 'buy', 'sell', 'hold', "
    "'consider entering', 'you should'). Describe the situation, don't direct the reader.\n"
    "- Plain text only: no markdown, no bullet points, no headers, no emoji, no disclaimers, "
    "and no preamble like 'Here is the analysis:' — output only the note itself.\n"
    "- Be terse. This is one line of dashboard narration, not an essay."
)


CHART_INSIGHT_SYSTEM_PREAMBLE = (
    "You are the chart-commentary voice on a live trading dashboard. A rule-based technical "
    "engine has already computed trend, momentum, RSI, EMA positioning, and the nearest "
    "support/resistance level for one symbol. Your job is to turn those numbers into a single "
    "natural-language observation a trader can read in under two seconds — the kind of line a "
    "sharp desk analyst would say out loud while glancing at a chart, not a report.\n\n" + COMMON_RULES
)


RESEARCH_SYSTEM_PREAMBLE = (
    "You are the research-summary voice for a company snapshot card. You are handed the "
    "technical picture (trend/momentum) plus a short list of catalysts and an earnings-timing "
    "flag, all already derived elsewhere. Your job is to weave those into one short, neutral "
    "summary of 'what's going on with this name right now' — grounded entirely in the listed "
    "catalysts, never speculating about ones that aren't listed.\n\n" + COMMON_RULES
)


TRADE_PLAN_SYSTEM_PREAMBLE = (
    "You are the analyst's-take voice on a trade plan card. A risk-sizing engine has already "
    "fully decided the setup: direction, entry, stop, first target, and risk:reward — none of "
    "that is open for discussion. Your job is to add one short, plain-English line of color "
    "that helps the reader understand the setup's risk profile at a glance (e.g. how tight the "
    "stop is relative to the reward, or what the confidence score implies) without repeating "
    "every number verbatim and without ever suggesting a different entry, stop, target, or "
    "position size than the ones given.\n\n" + COMMON_RULES
)


def build_chart_insight_prompt(symbol: str, chart: ChartAnalysis) -> str:
    levels = ""
    if chart.trend == "Bullish" and chart.resistance:
        levels = f"Nearest resistance: ${chart.resistance[0]:.2f}."
    elif chart.trend == "Bearish" and chart.support:
        levels = f"Nearest support: ${chart.support[0]:.2f}."
    return (
        f"{CHART_INSIGHT_SYSTEM_PREAMBLE}\n\n"
        "Data:\n"
        f"Symbol: {symbol}\nPrice: ${chart.price:.2f}\nTrend: {chart.trend}\nMomentum: {chart.momentum}\n"
        f"RSI(14): {chart.rsi14:.0f}\nPrice vs EMA20: {chart.pct_from_ema20 * 100:+.1f}%\n{levels}\n\n"
        "Write the chart insight note now (1-2 sentences, plain text, nothing else)."
    )


def build_research_summary_prompt(
    symbol: str, chart: ChartAnalysis, has_upcoming_earnings: bool, catalysts: list[str]
) -> str:
    catalysts_txt = "; ".join(catalysts) if catalysts else "none listed"
    earnings_txt = "Earnings are upcoming." if has_upcoming_earnings else "No earnings imminent."
    return (
        f"{RESEARCH_SYSTEM_PREAMBLE}\n\n"
        "Data:\n"
        f"Symbol: {symbol}\nTrend: {chart.trend}\nMomentum: {chart.momentum}\n{earnings_txt}\n"
        f"Catalysts: {catalysts_txt}\n\n"
        "Write the research summary note now (1-2 sentences, plain text, nothing else)."
    )


def build_trade_plan_take_prompt(
    symbol: str,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    rr1: float,
    confidence_score: int,
    chart: ChartAnalysis,
    volume_ratio: float,
    signal_reasons: list[str] | None = None,
) -> str:
    risk_per_share = abs(entry - stop)
    reward_per_share = abs(tp1 - entry)
    key_level = chart.support[0] if direction == "long" and chart.support else (chart.resistance[0] if chart.resistance else None)
    key_level_label = "support" if direction == "long" else "resistance"
    key_level_txt = f"Nearest {key_level_label} the stop is set against: ${key_level:.2f}.\n" if key_level is not None else ""
    fundamentals_txt = (
        f"Fundamental/news signals (already scored by a separate rule-based engine): {'; '.join(signal_reasons)}.\n"
        if signal_reasons
        else ""
    )
    return (
        f"{TRADE_PLAN_SYSTEM_PREAMBLE}\n\n"
        "Data:\n"
        f"Symbol: {symbol}\nDirection: {direction}\nEntry: ${entry:.2f}\nStop: ${stop:.2f}\n"
        f"Target 1: ${tp1:.2f}\nRisk per share: ${risk_per_share:.2f}\nReward per share (to target 1): "
        f"${reward_per_share:.2f}\nRisk:Reward: {rr1:.1f}:1\nRule-based confidence score: {confidence_score}%\n"
        f"Underlying trend: {chart.trend}\nMomentum: {chart.momentum}\nRSI(14): {chart.rsi14:.0f}\n"
        f"Price vs 20-day EMA: {chart.pct_from_ema20 * 100:+.1f}%\nVolume vs 20-day average: {volume_ratio:.1f}x\n"
        f"{key_level_txt}{fundamentals_txt}\n"
        "Write the trade plan's 'analyst take' note now (1-2 sentences, plain text, nothing else). Explain "
        "briefly WHY this setup exists — reference the trend/momentum/RSI/volume context (and the "
        "fundamental/news signals, if any are listed) — not just restate the entry/stop/target numbers, "
        "which the reader can already see elsewhere on the card."
    )
