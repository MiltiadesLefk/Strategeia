from __future__ import annotations


# Kept as its own small, minor factor (cap +/-1) rather than folded into
# fundamental_score — options-chain coverage is patchy on free data (many
# smaller names and all crypto pairs have none at all), so this should never
# be able to swing a decision on its own, only nudge one that's already
# supported by other evidence. Named thresholds are the conventional
# CBOE-style put/call volume ratio reads: below ~0.7 is call-heavy/
# bullish-skewed positioning, above ~1.0 is put-heavy/bearish-skewed; the
# band between is unremarkable and contributes nothing.
OPTIONS_SCORE_CAP = 1
PUT_CALL_BULLISH_THRESHOLD = 0.7
PUT_CALL_BEARISH_THRESHOLD = 1.0

