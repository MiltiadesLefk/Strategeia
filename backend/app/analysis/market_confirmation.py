from __future__ import annotations

from app.analysis.trend import ChartAnalysis

# Two independent confluence checks the scanner/trade-plan pipeline didn't
# have before: does the weekly timeframe agree with the daily signal
# ("trade with the higher timeframe"), and does the broad market agree
# (don't fight the overall tape). Deliberately kept OUT of
# scanner_scoring.score_symbol's own 0-6 point system and signal tiers
# (potential_setup/watching/no_signal) — folding it in there would change
# the Market Scanner's score/signal for every symbol and require
# recalibrating POTENTIAL_SETUP_SCORE/WATCHING_SCORE. Instead this is a
# separate, independently-capped dimension added only into
# trade_plan_service's confidence math, the same pattern
# fundamental_score/news_score already use. See notes/Decisions.md.
MARKET_CONFIRMATION_SCORE_CAP = 2

# SPY, not ^GSPC — same liquid, always-listed proxy health_monitor.py
# already uses elsewhere in this codebase for exactly this "is the market
# data pipeline even working" role.
MARKET_PROXY_SYMBOL = "SPY"


def score_market_confirmation(
    direction: str | None,
    weekly_chart: ChartAnalysis | None,
    market_chart: ChartAnalysis | None,
) -> tuple[int, list[str]]:
    """+/-1 for weekly-timeframe agreement, +/-1 for broad-market agreement,
    clamped to +/-MARKET_CONFIRMATION_SCORE_CAP. A missing chart (fetch
    failed) or a Neutral one (no opinion to confirm or contradict) both
    contribute 0 — a data gap or genuine indecision is never scored as
    disagreement."""
    if direction is None:
        return 0, []

    score = 0
    reasons: list[str] = []

    if weekly_chart is not None and weekly_chart.trend != "Neutral":
        weekly_direction = "long" if weekly_chart.trend == "Bullish" else "short"
        if weekly_direction == direction:
            score += 1
            reasons.append(f"weekly timeframe also {weekly_chart.trend.lower()}")
        else:
            score -= 1
            reasons.append(f"against the weekly trend ({weekly_chart.trend.lower()})")

    if market_chart is not None and market_chart.trend != "Neutral":
        market_direction = "long" if market_chart.trend == "Bullish" else "short"
        if market_direction == direction:
            score += 1
            reasons.append(f"broad market ({MARKET_PROXY_SYMBOL}) also {market_chart.trend.lower()}")
        else:
            score -= 1
            reasons.append(f"against the broad market trend ({market_chart.trend.lower()})")

    return max(-MARKET_CONFIRMATION_SCORE_CAP, min(MARKET_CONFIRMATION_SCORE_CAP, score)), reasons


# VIX regime: a one-directional risk flag, not a confluence check — there's
# no "VIX agrees with my direction," only "is this a choppier/less reliable
# tape to be trend-following in right now." Kept deliberately small (cap 1)
# and one-sided (only ever a penalty, never a bonus for calm — "normal" is
# the neutral read, not something to reward) so one macro-wide number can't
# swing a symbol-specific decision much on its own. 25 is the conventional
# "elevated/fearful" VIX threshold (CBOE's own historical framing); no
# separate "extreme" tier — this app doesn't need finer granularity than
# "proceed with more caution" for a minor contributing factor.
VIX_REGIME_SCORE_CAP = 1
VIX_ELEVATED_THRESHOLD = 25.0
VIX_PROXY_SYMBOL = "^VIX"


def score_vix_regime(vix_level: float | None) -> tuple[int, list[str]]:
    """-VIX_REGIME_SCORE_CAP when VIX is elevated (>= 25), else 0. A missing
    VIX read (fetch failed) also contributes 0 — never guessed at."""
    if vix_level is None or vix_level < VIX_ELEVATED_THRESHOLD:
        return 0, []
    return -VIX_REGIME_SCORE_CAP, [f"VIX elevated ({vix_level:.1f}) — broad market volatility/fear regime"]
