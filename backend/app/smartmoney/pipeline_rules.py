"""Named constants of the smart-money evaluation pipeline (NEW_PLAN section 6 steps 4-8 and section 7).

Plain values only (no imports of app code), so `portfolio` and `services` can read them without a cycle.
They are part of the strategy fingerprint (`strategy/snapshot.py`).
"""

from __future__ import annotations

# The two paper accounts of the pipeline (the third, core, is the ordinary daily scan's). Created by an
# explicit action (`services/sleeve_service.ensure_smart_money_sleeves`), never by a read.
AUTO_SLEEVE_KEY = "smart-money-auto"
AUTO_SLEEVE_NAME = "Smart money (auto)"
MANUAL_SLEEVE_KEY = "smart-money-manual"
MANUAL_SLEEVE_NAME = "Smart money (manual)"
SMART_MONEY_STYLE = "smart-money"
SMART_MONEY_SLEEVE_KEYS = (AUTO_SLEEVE_KEY, MANUAL_SLEEVE_KEY)

# Where a plan/position says it came from (TradePlanRecord.source).
SOURCE_AUTO = "smart_money"
SOURCE_REVIEW_BUY = "smart_money_review"

# "New positions per week" counts positions opened in the sleeve over this many days.
NEW_POSITION_WINDOW_DAYS = 7

# The exit scan of a smart-money sleeve reads this much daily history instead of the core's "3mo": the time
# limit there (smart_money_max_holding_days, up to 200 trading days) is longer than 3 months of bars.
SMART_MONEY_EXIT_SCAN_PERIOD = "1y"

# Liquidity floor input: the average of close x volume over this many final daily bars.
LIQUIDITY_LOOKBACK_BARS = 20
# Fewer bars than this and the average dollar volume says nothing: treated as no prices.
LIQUIDITY_MIN_BARS = 5

# Event-exit review (alert only): an 8-K or earnings report counts from this many days before now.
EVENT_REVIEW_LOOKBACK_DAYS = 10
# An earnings result this far under consensus (percent) is a miss (the same size the scorers call noise below).
EARNINGS_MISS_PCT = 1.0
