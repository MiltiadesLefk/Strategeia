from __future__ import annotations




# Same shape as fundamental_scoring's revenue-growth check: a real historical
# pattern, direction-aware, small and capped. Requires a minimum sample —
# fewer than this many reported quarters is too small a sample for "beats
# consistently" to mean anything rather than a coin-flip streak.
SURPRISE_TRACK_RECORD_CAP = 1
MIN_QUARTERS_FOR_TRACK_RECORD = 4
# Consensus beats/misses by less than this are noise — a company reporting
# $2.00 against a $1.99 estimate has not meaningfully beaten anything.
MEANINGFUL_SURPRISE_PCT = 1.0
# Fraction of sampled quarters that must agree for the pattern to count as
# "consistent" rather than a mixed bag.
CONSISTENCY_THRESHOLD = 0.75

# How many trading days after a report date to look for the market's
# reaction. Most US companies report either before the open or after the
# close, so the NEXT bar (not the report-date bar itself, which may still be
# mid-reaction intraday on a same-day report) is the cleanest single-bar read
# of "how much did this actually move."
REACTION_LOOKAHEAD_DAYS = 3

