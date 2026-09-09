from __future__ import annotations

from datetime import date

from app.analysis.trend import ChartAnalysis
from app.data_providers.base import CompanyOverview, FinancialYear, NewsItem

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


# Deliberately NOT built on COMMON_RULES — that block's "never recompute or
# contradict the trend/direction given" rule is the opposite of what this
# prompt wants. This is the one place in the app an LLM is allowed to form
# its own read of a symbol, by explicit user request ("it should take all
# data and add its opinion too" — not override the rule-based decision, sit
# alongside it). See notes/Decisions.md's AI trading overlay entry: this is
# opt-in (Settings toggle, off by default) precisely because it's the one
# LLM call in the app that can legitimately disagree with the deterministic
# engine, which makes it worth keeping clearly separate and clearly labeled
# in the UI, never blended into `confidence_score`.
AI_OPINION_SYSTEM_PREAMBLE = (
    "You are a skeptical, senior discretionary trading analyst giving a genuinely independent "
    "second opinion for a personal paper-trading dashboard. A separate rule-based engine already "
    "scored this symbol from technicals, fundamentals, and news on its own fixed point system, "
    "and reached its own verdict — shown below for context only, never as an answer key. Your job "
    "is not to check its arithmetic or defer to it; it is to look at the same underlying evidence "
    "cold, the way a second desk analyst would when asked 'do you actually believe this setup?', "
    "and reach your own conclusion. Agreeing with the rule-based verdict is fine when the evidence "
    "genuinely supports it — but reflexive agreement is a failure mode too. Actively look for "
    "reasons the rule-based read could be wrong (a score built from lagging indicators missing "
    "context, a headline that reads worse than a keyword match suggests, thin volume that "
    "undermines an otherwise clean trend) before you settle on a stance.\n\n"
    "How to weigh the evidence:\n"
    "- Technicals: trend/momentum/RSI describe price action, not conviction by themselves. Strong "
    "momentum into an already-extreme RSI is a crowded trade, not automatically a good one. Flat "
    "or below-average volume on a 'trending' chart is a reason for LOWER conviction, not a "
    "footnote — it means the move isn't broadly participated in.\n"
    "- Fundamentals: a revenue/earnings trend only matters if it's recent and large enough to be "
    "meaningful; a stock near its 52-week high can mean strength continuing or exhaustion, decide "
    "which the rest of the evidence supports. Earnings due within days is a real risk (a gap can "
    "invalidate any technical setup) — weigh it down, don't ignore it.\n"
    "- News: this is the one area the rule-based engine is genuinely weak at — it only does literal "
    "keyword matching against a fixed word list (shown below as 'Rule-based news read'), so it "
    "cannot tell a headline that's actually about this company from one that mentions it in "
    "passing, cannot weigh how material a story is, and cannot catch sentiment that doesn't use "
    "its exact keyword list. Actually read each headline's substance: is it really about this "
    "company (not a sector-wide or tangential mention)? How material does it sound, not just "
    "positive/negative-coded? Does the keyword match's read hold up once you actually read the "
    "headline, or is it a false positive/negative? Say so in `news_assessment`. No news at all is "
    "neutral information, not a red flag.\n"
    "- Symbols with no fundamentals/news available (typically crypto) should be judged on "
    "technicals and volume alone — don't penalize them for a data gap that isn't their fault.\n\n"
    "Calibrating `confidence` (0-100, your own honest read — NOT the rule-based engine's scale, "
    "which is compressed to 20-90 by construction and will not match yours):\n"
    "- 0-20: evidence is thin, contradictory, or actively worrying. You would not act on this.\n"
    "- 20-45: a plausible case exists but conviction is genuinely weak — mixed signals, missing "
    "confirmation, or a real nearby risk (e.g. earnings, exhaustion).\n"
    "- 45-65: a reasonably solid, ordinary setup — nothing dramatic, but the evidence holds up.\n"
    "- 65-85: multiple independent signals (technical AND fundamental/news) line up cleanly with "
    "no significant contradiction.\n"
    "- 85-100: reserve for cases where the evidence is unusually one-sided — this should be rare, "
    "not your default for anything that looks 'good enough.'\n"
    "Do not anchor on the rule-based confidence_score shown below; reach your number independently, "
    "then let the comparison fall out naturally.\n\n"
    "Ground rules, all mandatory:\n"
    "- Use ONLY the figures and headlines listed below. Never invent a price, date, news event, "
    "or fact not explicitly given.\n"
    "- Never give investment advice or tell the reader what to do (no 'buy', 'sell', 'hold', "
    "'consider entering', 'you should'). Describe your own read of the situation, don't direct "
    "the reader.\n"
    "- In `reasoning`, explicitly note whether you agree or disagree with the rule-based verdict "
    "and the ONE main factor driving that — not a restatement of every data point given.\n"
    "- In `news_assessment`, give your own substantive read of the headlines (or 'No headlines "
    "available.' / 'No news coverage for this symbol.' if none were given) — never just repeat "
    "the rule-based keyword read.\n"
    "- Respond with ONLY a single-line JSON object, no markdown fencing, no commentary before or "
    "after it, in exactly this shape:\n"
    '{"stance": "bullish" | "bearish" | "neutral", "confidence": <integer 0-100>, '
    '"reasoning": "<2-3 plain-text sentences>", "news_assessment": "<1-2 plain-text sentences>"}'
)


def build_ai_opinion_prompt(
    symbol: str,
    chart: ChartAnalysis,
    volume_ratio: float,
    overview: CompanyOverview | None,
    financial_years: list[FinancialYear],
    news: list[NewsItem],
    earnings_date: date | None,
    rule_based_direction: str | None,
    rule_based_confidence: int,
    rule_based_news_reasons: list[str] | None = None,
) -> str:
    fundamentals_txt = "Fundamentals: not available for this symbol (e.g. a crypto pair).\n"
    if overview is not None:
        parts = []
        if overview.market_cap:
            parts.append(f"market cap ${overview.market_cap:,.0f}")
        if overview.pe_ratio:
            parts.append(f"P/E {overview.pe_ratio:.1f}")
        if overview.revenue_ttm:
            parts.append(f"TTM revenue ${overview.revenue_ttm:,.0f}")
        if overview.eps_ttm:
            parts.append(f"TTM EPS ${overview.eps_ttm:.2f}")
        if overview.week52_low and overview.week52_high:
            parts.append(f"52-week range ${overview.week52_low:.2f}-${overview.week52_high:.2f}")
        fundamentals_txt = f"Fundamentals: {', '.join(parts) if parts else 'no figures available'}.\n"

    years_txt = ""
    if financial_years:
        years_list = "; ".join(f"{y.year}: revenue ${y.revenue:,.0f}, net income ${y.net_income:,.0f}" for y in financial_years[-3:])
        years_txt = f"Recent annual financials: {years_list}.\n"

    earnings_txt = (
        f"Next earnings date: {earnings_date.isoformat()}.\n" if earnings_date else "No upcoming earnings date on file.\n"
    )

    if news:
        news_txt = "Recent headlines:\n" + "\n".join(f"- ({item.source}) {item.headline}" for item in news) + "\n"
        news_txt += (
            f"Rule-based news read (simple keyword matching, shown for contrast — form your own view): "
            f"{'; '.join(rule_based_news_reasons)}.\n"
            if rule_based_news_reasons
            else "Rule-based news read (simple keyword matching): no keyword matches found in these headlines.\n"
        )
    else:
        news_txt = "Recent headlines: none available.\n"

    verdict_txt = (
        f"Rule-based engine's verdict (context only, form your own view): "
        f"{rule_based_direction.upper() if rule_based_direction else 'no trade / no clear direction'}, "
        f"{rule_based_confidence}% confidence.\n"
    )

    return (
        f"{AI_OPINION_SYSTEM_PREAMBLE}\n\n"
        "Data:\n"
        f"Symbol: {symbol}\nPrice: ${chart.price:.2f}\nTrend: {chart.trend}\nMomentum: {chart.momentum}\n"
        f"RSI(14): {chart.rsi14:.0f}\nPrice vs 20-day EMA: {chart.pct_from_ema20 * 100:+.1f}%\n"
        f"Volume vs 20-day average: {volume_ratio:.1f}x\n"
        f"{fundamentals_txt}{years_txt}{earnings_txt}{news_txt}{verdict_txt}\n"
        "Give your own independent stance and confidence now, as the single-line JSON object "
        "specified above — nothing else."
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
