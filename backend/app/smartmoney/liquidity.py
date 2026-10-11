"""The liquidity floor (NEW_PLAN section 6, step 4): a realism constraint, not a judgement of the insiders.

A candidate whose last close is under `LIQUIDITY_MIN_PRICE` or whose 20-day average dollar volume is under
`LIQUIDITY_MIN_DOLLAR_VOLUME` is not given an evaluation (it would cost an AI-narrated evaluation for a name
the paper account could not realistically trade). No usable prices at all is its own verdict: the symbol is
never guessed at. Pure: it reads a frame the caller fetched, with the bars final at the caller's moment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from app.markets import is_daily_bar_final
from app.smartmoney.pipeline_rules import LIQUIDITY_LOOKBACK_BARS, LIQUIDITY_MIN_BARS
from app.smartmoney.scoring import LIQUIDITY_MIN_DOLLAR_VOLUME, LIQUIDITY_MIN_PRICE

REASON_ILLIQUID = "illiquid"
REASON_NO_PRICES = "no_prices"


@dataclass(frozen=True)
class LiquidityVerdict:
    ok: bool
    reason: str | None  # REASON_* when not ok
    detail: str
    last_close: float | None = None
    avg_dollar_volume: float | None = None


def _final_bars(symbol: str, bars: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """The bars that are final at `now` (the day still being traded is not: its volume is partial)."""
    if "date" not in bars.columns:
        return bars
    keep = [is_daily_bar_final(symbol, pd.Timestamp(d).date(), now) for d in bars["date"]]
    return bars[keep]


def check_liquidity(symbol: str, bars: pd.DataFrame | None, now: datetime) -> LiquidityVerdict:
    if bars is None or bars.empty or not {"close", "volume"} <= set(bars.columns):
        return LiquidityVerdict(False, REASON_NO_PRICES, "No daily prices could be read for this symbol.")
    usable = _final_bars(symbol, bars, now).dropna(subset=["close"])
    if len(usable) < LIQUIDITY_MIN_BARS:
        return LiquidityVerdict(False, REASON_NO_PRICES, f"Only {len(usable)} usable daily bars (needs {LIQUIDITY_MIN_BARS}).")
    recent = usable.tail(LIQUIDITY_LOOKBACK_BARS)
    last_close = float(recent["close"].iloc[-1])
    avg_dollar = float((recent["close"].astype(float) * recent["volume"].fillna(0).astype(float)).mean())
    if last_close < LIQUIDITY_MIN_PRICE:
        return LiquidityVerdict(
            False, REASON_ILLIQUID, f"Last close ${last_close:,.2f} is under the ${LIQUIDITY_MIN_PRICE:,.2f} floor.",
            last_close, avg_dollar,
        )
    if avg_dollar < LIQUIDITY_MIN_DOLLAR_VOLUME:
        return LiquidityVerdict(
            False, REASON_ILLIQUID,
            f"Average daily dollar volume ${avg_dollar:,.0f} over {len(recent)} days is under ${LIQUIDITY_MIN_DOLLAR_VOLUME:,.0f}.",
            last_close, avg_dollar,
        )
    return LiquidityVerdict(True, None, "Liquid enough.", last_close, avg_dollar)
