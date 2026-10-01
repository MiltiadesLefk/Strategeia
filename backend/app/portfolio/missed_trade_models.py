"""The stored result of the missed-trades counterfactual (see missed_trades.py).

One row per trade plan that was NOT executed (a no-trade decision, an AI veto, a
plan held back or refused). It is a cache of a computation, not a record of
anything that happened: deleting the table loses nothing that cannot be rebuilt
from the plans and the price history.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.timeutil import utcnow_naive

# What became of the hypothetical trade.
OUTCOME_RESOLVED = "resolved"  # a stop, TP1 or the time limit was reached: final, never recomputed
OUTCOME_OPEN = "open"  # no exit yet: marked at the latest close, recomputed on later refreshes
OUTCOME_NO_DATA = "no_data"  # the price history could not be loaded: retried on later refreshes
OUTCOME_NOT_SIMULATABLE = "not_simulatable"  # no trade can be built from the data of that day: final


class MissedTradeOutcome(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # One row per plan. Unique so a refresh that runs twice (or two at once)
    # updates the row instead of duplicating a trade in the totals.
    plan_id: int = Field(foreign_key="tradeplanrecord.id", unique=True, index=True)
    symbol: str
    # The category the plan had when it was computed (the report re-derives it
    # from the plan row, so a plan that changes state is never counted twice).
    category: str
    status: str = Field(default=OUTCOME_OPEN, index=True)

    # The hypothetical trade. entry is the price before slippage; fill_price is
    # what the paper engine's entry slippage would have made of it.
    direction: Optional[str] = None
    entry: Optional[float] = None
    fill_price: Optional[float] = None
    stop: Optional[float] = None
    tp1: Optional[float] = None
    # "plan" = the plan's own stored entry/stop/target (a plan that was written),
    # "reconstructed" = rebuilt with the live rules from the bars known then.
    trade_source: Optional[str] = None
    # Where the direction came from: "plan", "reason" (named in the decision's own
    # text) or "trend" (rebuilt from the chart).
    direction_source: Optional[str] = None

    # The result. r_multiple is net of slippage and commission, like close_position's.
    # For an open row it is the mark at the latest close, never a final number.
    r_multiple: Optional[float] = None
    exit_reason: Optional[str] = None  # stop_hit | tp1_hit | time_exit (None while open)
    exit_price: Optional[float] = None
    exit_date: Optional[date] = None
    mark_price: Optional[float] = None  # the latest close used for an open row

    # How the answer was reached, kept so nobody has to guess later.
    entry_bar_date: Optional[date] = None  # the last bar known at the decision (its close is the entry)
    bars_known: Optional[int] = None  # bars the reconstruction could see (nothing after the decision)
    bars_walked: Optional[int] = None  # bars after the decision the exit rules were run over
    resolution: Optional[str] = None  # "daily": daily bars only, no hourly refinement
    note: Optional[str] = None
    computed_at: datetime = Field(default_factory=utcnow_naive)
