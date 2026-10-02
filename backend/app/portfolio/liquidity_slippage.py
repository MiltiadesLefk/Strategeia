"""Liquidity-aware slippage: extra cost on a market fill that grows with how
much of the symbol's average daily volume the order is.

Off by default (`AppSettings.liquidity_slippage_enabled`): the flat
`slippage_bps` is then the whole story, exactly as before. When on, the engine
adds `impact_bps(...)` on top of it for every market fill (entries, stop exits
and time exits; never a take-profit, which is a resting limit).
"""

from __future__ import annotations

import math

# Hard ceiling on the extra bps, whatever the participation. WHY: the square-root
# law is only trustworthy for orders that are a small slice of the day's volume
# (the position-size cap already keeps paper orders to ~1% of ADV). Beyond that the
# formula would keep growing while the real answer is "you couldn't have traded
# this size", which is the size cap's job, not a slippage number's. 100 bps (1%)
# stops one thin name from turning a result into an artefact of the formula.
MAX_LIQUIDITY_SLIPPAGE_BPS = 100.0

# Default for `liquidity_slippage_coefficient`: the extra bps an order of 100% of
# ADV would pay before the cap. With 100, a 1% of ADV order pays 10 bps extra and
# a 0.01% order pays 1 bp. A rough, deliberately round figure, not a calibration:
# raise it for thinner names.
DEFAULT_LIQUIDITY_COEFFICIENT = 100.0


def impact_bps(shares: float, avg_daily_volume: float | None, coefficient: float) -> float:
    """Extra slippage in bps for an order of `shares` against `avg_daily_volume`:
    `coefficient * sqrt(shares / ADV)`, capped at MAX_LIQUIDITY_SLIPPAGE_BPS.

    Square root, not linear: market impact is empirically concave in order size
    (the usual square-root law), so doubling the order costs ~41% more, not 100%.
    No usable ADV (missing, zero) or no size gives 0: a guess would be invented
    precision, the same stance as the size cap."""
    if coefficient <= 0 or shares <= 0 or not avg_daily_volume or avg_daily_volume <= 0:
        return 0.0
    return min(coefficient * math.sqrt(shares / avg_daily_volume), MAX_LIQUIDITY_SLIPPAGE_BPS)
