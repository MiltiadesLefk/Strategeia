"""Trade-excursion statistics: what the best and worst price during each closed
trade (MFE / MAE, in R) say about the strategy, split into winners and losers.

Every number comes from closed rows that actually have excursion figures. Rows
closed before the figures existed, or whose bars were unavailable, are counted
out (`measured` vs `closed_trades`), never filled in. Averages over no rows are
None, not zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from app.portfolio.models import PaperPosition

# A loser that had reached this many R in the trade's favour before turning is
# a trade that was well ahead and still lost: the question is whether the exit
# was too slow, not whether the idea was wrong.
LOSER_WAS_AHEAD_R = 1.0

# A winner whose worst moment came this close to the stop (as a share of the
# initial risk) nearly lost instead: if many winners look like that, the stops
# are probably too tight, and a normal wobble would have stopped them out.
WINNER_NEAR_STOP_R = 0.8


@dataclass
class ExcursionGroup:
    n: int = 0
    avg_mfe_r: float | None = None  # how far in favour the trade got, on average
    avg_mae_r: float | None = None  # how far against it went, on average


@dataclass
class ExcursionStats:
    closed_trades: int = 0
    measured: int = 0  # closed trades that have excursion figures (the sample for all below)
    winners: ExcursionGroup = field(default_factory=ExcursionGroup)
    losers: ExcursionGroup = field(default_factory=ExcursionGroup)
    # Mean of realized R / MFE R over winners: how much of the best price the trade
    # reached was actually banked. Under this app's rule of closing the whole position
    # at TP1 a winner exits at its target, so this sits near 100% by construction;
    # it only tells you something once other exits (time limit, manual) win trades.
    exit_efficiency: float | None = None
    exit_efficiency_n: int = 0
    losers_reached_1r: int = 0  # losers whose MFE was at least LOSER_WAS_AHEAD_R
    winners_near_stop: int = 0  # winners whose MAE was at least WINNER_NEAR_STOP_R


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _group(rows: list[PaperPosition]) -> ExcursionGroup:
    return ExcursionGroup(
        n=len(rows),
        avg_mfe_r=_mean([p.mfe_r for p in rows]),
        avg_mae_r=_mean([p.mae_r for p in rows]),
    )


def compute_excursion_stats(closed: Iterable[PaperPosition]) -> ExcursionStats:
    closed = list(closed)
    measured = [p for p in closed if p.mfe_r is not None and p.mae_r is not None and p.realized_pnl is not None]
    # Same split as the win rate: a win is a positive net P&L, everything else is not.
    winners = [p for p in measured if p.realized_pnl > 0]
    losers = [p for p in measured if p.realized_pnl <= 0]
    efficiencies = [p.realized_r / p.mfe_r for p in winners if p.realized_r is not None and p.mfe_r > 0]
    return ExcursionStats(
        closed_trades=len(closed),
        measured=len(measured),
        winners=_group(winners),
        losers=_group(losers),
        exit_efficiency=_mean(efficiencies),
        exit_efficiency_n=len(efficiencies),
        losers_reached_1r=sum(1 for p in losers if p.mfe_r >= LOSER_WAS_AHEAD_R),
        winners_near_stop=sum(1 for p in winners if p.mae_r >= WINNER_NEAR_STOP_R),
    )
