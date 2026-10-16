"""FINRA short-sale volume as a silent signal (see analysis/shadow_signals.py).

What it reads: the share of a stock's recent traded volume that was sold short,
compared with that stock's own usual share. An unusually HIGH share is read as
mildly bearish context: it argues against a long and mildly supports a short.

What it is not: this is short-sale VOLUME, not short INTEREST. Market makers
sell short all day as part of providing liquidity, and short volume is
typically 40-50% of all volume for ordinary stocks. So only a clear rise over
the symbol's own baseline means anything, and even then it is a weak hint. The
points below are recorded, never added to confidence, until a backtest shows
the signal beats luck.

No stored data, or a reading too old to describe today's market, is reported
as unavailable; nothing is guessed.
"""

from __future__ import annotations

from sqlmodel import Session

from app.analysis.shadow_signals import ShadowContext, ShadowSignal, shadow_signal
from app.signals.finra import ShortVolumeReading, reading_is_fresh, short_volume_ratio_as_of

SIGNAL_NAME = "finra_short_volume"
# Largest swing this signal would add in either direction.
SHORT_VOLUME_SCORE_CAP = 1
# "Unusually high": the recent ratio is at least this many percentage points
# (as a fraction: 0.05 = 5 points) above the stock's own baseline median.
HIGH_RATIO_DELTA = 0.05


def score_short_volume(direction: str | None, ratio: float | None, baseline: float | None) -> tuple[int, str]:
    """(points, reason) for a recent short-volume ratio against its baseline.

    Positive supports the trade, negative argues against it. Zero when there is
    no direction, no ratio, no baseline, or the ratio is not unusually high.
    """
    if ratio is None:
        return 0, "No short-volume data."
    if baseline is None:
        return 0, f"Short volume is {ratio:.0%} of volume; too little history for a baseline yet."
    if direction not in ("long", "short"):
        return 0, f"Short volume is {ratio:.0%} of volume (baseline {baseline:.0%}); no trade direction to sign it by."
    delta = ratio - baseline
    if delta < HIGH_RATIO_DELTA:
        return 0, f"Short volume is {ratio:.0%} of volume, in line with its {baseline:.0%} baseline."
    sign = -1 if direction == "long" else 1
    points = max(-SHORT_VOLUME_SCORE_CAP, min(SHORT_VOLUME_SCORE_CAP, sign))
    stance = "against a long" if direction == "long" else "in favour of a short"
    return points, (
        f"Short volume is {ratio:.0%} of volume vs a {baseline:.0%} baseline: unusually high, "
        f"mildly bearish context ({stance}). Short-sale volume is not short interest and is "
        f"dominated by market makers."
    )


def build_short_volume_signal(direction: str | None, reading: ShortVolumeReading | None, fresh: bool = True) -> ShadowSignal:
    """Turn a stored reading into the recorded signal (pure: no database)."""
    if reading is None:
        return ShadowSignal(SIGNAL_NAME, None, 0, "No FINRA short-volume data stored for this symbol.", available=False)
    if not fresh:
        return ShadowSignal(
            SIGNAL_NAME, None, 0,
            f"Latest stored short-volume day ({reading.latest_trade_date.isoformat()}) is too old to use.",
            available=False,
        )
    points, reason = score_short_volume(direction, reading.recent_ratio, reading.baseline_ratio)
    value = f"{reading.recent_ratio:.1%} short volume"
    if reading.baseline_ratio is not None:
        value += f" (baseline {reading.baseline_ratio:.1%})"
    return ShadowSignal(SIGNAL_NAME, value, points, reason, available=True)


def _read(session: Session | None, symbol: str):
    return short_volume_ratio_as_of(session, symbol) if session is not None else None


@shadow_signal(SIGNAL_NAME)
def _finra_shadow_scorer(context: ShadowContext) -> ShadowSignal:
    reading = _read(context.session, context.symbol)
    return build_short_volume_signal(context.direction, reading, fresh=reading is None or reading_is_fresh(reading))
