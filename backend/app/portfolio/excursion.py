"""Best and worst price during a trade (MFE / MAE), computed from the bars a
position was actually open.

Maximum favourable excursion (MFE) is the furthest the price moved IN the
trade's favour between entry and exit; maximum adverse excursion (MAE) is the
furthest it moved AGAINST it. Win rate can't answer the questions these can:
did the winners nearly get stopped out first (stops too tight)? did the losers
spend time well in profit before turning (exits too slow)?

The idea (and the entry-bar rule below) comes from the maximum favourable /
adverse excursion notebook in stefan-jansen/machine-learning-for-trading
(MIT); the code here is written for this app's own bars and exit rules.

Both numbers are non-negative magnitudes, whatever the direction: a long's MFE
is the highest price above entry, its MAE the lowest price below it; a short
mirrors that. Each is reported as a percent of the entry price and in R
(multiples of the position's initial risk, |entry - stop|), the unit the rest
of the app reasons in.

Honesty rules (the same ones the exit scan follows):

* Only bars the position was exposed to count. The entry bar is excluded (the
  entry is its close, so its range is already spent); the caller passes the bars
  AFTER it.
* Daily bars cannot order the high and the low within one day, so the bar a
  position EXITED on is handled with care. See `_observed_prices`.
* Nothing is fabricated: no bars at all (None) gives None, never a guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd

# How the last bar in `bars` relates to the moment the position left.
#   "complete" - the whole bar happened while the position was held (a time exit at
#                the bar's close, a manual close made right after fetching the bars,
#                or a position that is still open). Its high and low both count.
#   "exit"     - the position left DURING the last bar (a stop or TP1 hit, or a manual
#                close rebuilt later from finished bars). See _observed_prices.
LastBar = Literal["complete", "exit"]


@dataclass(frozen=True)
class Excursion:
    mfe_pct: float  # furthest favourable move, % of the entry price
    mae_pct: float  # furthest adverse move, % of the entry price
    # In multiples of the initial risk |entry - stop|. None when that risk is
    # zero or unknown, because a ratio to nothing is not a number.
    mfe_r: float | None
    mae_r: float | None
    bars_used: int  # bars after the entry bar that were inspected


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _observed_prices(bars: pd.DataFrame, exit_price: float | None, last_bar: LastBar) -> tuple[list[float], list[float]]:
    """(highs, lows): every price the position is known to have been exposed to.

    Bars before the last one, and every bar when `last_bar` is "complete",
    contribute their full high and low. The EXIT bar is different, because a daily
    bar cannot say which extreme came first and the position only lived through
    the part of the day before it left. It contributes just the open (the day's
    first price) and the exit fill. That is exactly right for a stop exit (the
    adverse extreme up to the exit IS the fill: the bar's low can be far lower, but
    after the position was gone) and for a target exit (the favourable extreme is
    the fill), and it is the cautious choice for the other side of the bar, which
    may have happened before or after the exit. Leaving it out can only make an
    excursion look smaller than it was, never flatter the trade."""
    highs: list[float] = []
    lows: list[float] = []
    count = len(bars)
    for number, (_, bar) in enumerate(bars.iterrows(), start=1):
        if number == count and last_bar == "exit":
            bar_open = _finite(bar.get("open"))
            if bar_open is not None:
                highs.append(bar_open)
                lows.append(bar_open)
            continue
        high, low = _finite(bar.get("high")), _finite(bar.get("low"))
        if high is not None:
            highs.append(high)
        if low is not None:
            lows.append(low)
    if exit_price is not None:
        # The fill is a price the position really traded at, whichever bar it was.
        highs.append(exit_price)
        lows.append(exit_price)
    return highs, lows


def compute_excursion(
    direction: str,
    entry_price: float,
    stop_loss: float,
    bars: pd.DataFrame | None,
    *,
    exit_price: float | None = None,
    last_bar: LastBar = "complete",
) -> Excursion | None:
    """MFE / MAE of one position over `bars` (the bars AFTER the entry bar, up
    to and including the exit bar for a closed position).

    `bars=None` means the bars were not available: returns None. An EMPTY frame
    is a real answer (the position opened and closed within the entry bar's
    session): the result is then built from the entry price and `exit_price`
    alone. `exit_price` is the fill the position closed at; it is always counted
    as a price the position traded at.

    The entry price itself was traded, so both excursions floor at zero."""
    if bars is None or entry_price is None or entry_price <= 0 or direction not in ("long", "short"):
        return None
    highs, lows = _observed_prices(bars, _finite(exit_price), last_bar)
    top = max([entry_price, *highs])
    bottom = min([entry_price, *lows])
    if direction == "long":
        mfe, mae = top - entry_price, entry_price - bottom
    else:
        mfe, mae = entry_price - bottom, top - entry_price
    risk = abs(entry_price - stop_loss) if stop_loss is not None else 0.0
    return Excursion(
        mfe_pct=mfe / entry_price * 100.0,
        mae_pct=mae / entry_price * 100.0,
        mfe_r=mfe / risk if risk > 0 else None,
        mae_r=mae / risk if risk > 0 else None,
        bars_used=len(bars),
    )
