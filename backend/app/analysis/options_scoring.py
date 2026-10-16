from __future__ import annotations

from app.data_providers.base import OptionsSummary

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


def score_options_positioning(direction: str | None, options: OptionsSummary | None) -> tuple[int, list[str]]:
    """+/-OPTIONS_SCORE_CAP depending on whether the options market's own
    put/call volume skew agrees or disagrees with `direction`. No chain
    available (crypto, thin names) or an unremarkable ratio both contribute
    0 — never a penalty for a data gap or genuine indecision."""
    if direction is None or options is None or options.put_call_volume_ratio is None:
        return 0, []

    ratio = options.put_call_volume_ratio
    if ratio < PUT_CALL_BULLISH_THRESHOLD:
        skew = "bullish"
    elif ratio > PUT_CALL_BEARISH_THRESHOLD:
        skew = "bearish"
    else:
        return 0, []

    skew_direction = "long" if skew == "bullish" else "short"
    if skew_direction == direction:
        return OPTIONS_SCORE_CAP, [f"options positioning {skew}-skewed (put/call volume {ratio:.2f})"]
    return -OPTIONS_SCORE_CAP, [f"options positioning against the setup ({skew}-skewed, put/call volume {ratio:.2f})"]
