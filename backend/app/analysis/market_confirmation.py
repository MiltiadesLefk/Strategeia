from __future__ import annotations


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

