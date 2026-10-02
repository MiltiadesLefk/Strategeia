from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field

from sqlalchemy import true
from sqlmodel import Session, select

from app.data_providers.base import AllProvidersFailedError, DataProvider
from app.portfolio.excursion_stats import compute_excursion_stats
from app.portfolio.models import AccountState, PaperPosition
from app.portfolio.sleeves import ALL_SLEEVES, SleeveScope, read_scope, scope_clause


@dataclass
class PortfolioStats:
    total_trades: int
    win_rate: float
    total_return: float
    avg_rr: float | None
    active_positions: int
    portfolio_value: float
    starting_cash: float
    current_cash: float
    # How closed trades ended: close_reason -> count (stop_hit, tp1_hit, time_exit,
    # manual). Counted from the closed rows themselves; a high share of time
    # exits means setups mostly stall instead of resolving, which is information
    # about the strategy that win rate alone hides.
    exit_reasons: dict[str, int] = field(default_factory=dict)
    # Best/worst price during closed trades (MFE/MAE), see portfolio/excursion_stats.py.
    excursions: dict = field(default_factory=dict)


def compute_portfolio_stats(
    session: Session,
    data_provider: DataProvider,
    default_starting_cash: float,
    sleeve: SleeveScope | str | None = None,
) -> PortfolioStats:
    """Every figure for ONE sleeve (the core sleeve by default), computed from that
    sleeve's own rows. `sleeve` is a scope, a sleeve key, or ALL_SLEEVES: the
    combined book, where cash and starting cash are summed over the accounts and
    the trades are pooled (so win rate and average R are over every sleeve's
    closed trades together). An unknown key raises SleeveError(404)."""
    if sleeve == ALL_SLEEVES:
        return _compute(session, data_provider, default_starting_cash, None)
    scope = sleeve if isinstance(sleeve, SleeveScope) else read_scope(session, sleeve)
    return _compute(session, data_provider, default_starting_cash, scope)


def _compute(
    session: Session, data_provider: DataProvider, default_starting_cash: float, scope: SleeveScope | None
) -> PortfolioStats:
    """`scope=None` is every sleeve together."""

    def in_scope(column):
        return scope_clause(column, scope) if scope is not None else true()

    if scope is None:
        accounts = session.exec(select(AccountState)).all()
        starting_cash = sum(a.starting_cash for a in accounts) if accounts else default_starting_cash
        current_cash = sum(a.current_cash for a in accounts) if accounts else default_starting_cash
    else:
        account = session.exec(select(AccountState).where(in_scope(AccountState.sleeve_id))).first()
        # A sleeve that never traded has no account row yet: it will be seeded with
        # its own starting cash (core: the Settings value).
        seed = scope.starting_cash if scope.starting_cash is not None else default_starting_cash
        starting_cash = account.starting_cash if account else seed
        current_cash = account.current_cash if account else seed

    closed = session.exec(
        select(PaperPosition).where(PaperPosition.status == "closed", in_scope(PaperPosition.sleeve_id))
    ).all()
    total_trades = len(closed)
    wins = sum(1 for p in closed if (p.realized_pnl or 0) > 0)
    win_rate = (wins / total_trades * 100) if total_trades else 0.0
    rr_values = [p.realized_r for p in closed if p.realized_r is not None]
    avg_rr = (sum(rr_values) / len(rr_values)) if rr_values else None

    open_positions = session.exec(
        select(PaperPosition).where(PaperPosition.status == "open", in_scope(PaperPosition.sleeve_id))
    ).all()
    mark_value = 0.0
    for position in open_positions:
        try:
            price = data_provider.get_quote(position.symbol).price
        except AllProvidersFailedError:
            price = position.entry_price
        # See PaperTradingEngine._record_equity_snapshot: a short's cash was
        # already increased by shares*entry at open, so its mark-to-market
        # contribution here is the (negative) cost to buy back and cover —
        # summing shares*price for both directions double-counts a short's
        # notional and wildly inflates portfolio_value/total_return.
        mark_value += position.shares * price if position.direction == "long" else -(position.shares * price)

    portfolio_value = current_cash + mark_value
    total_return = ((portfolio_value - starting_cash) / starting_cash * 100) if starting_cash else 0.0

    return PortfolioStats(
        total_trades=total_trades,
        win_rate=win_rate,
        total_return=total_return,
        avg_rr=avg_rr,
        active_positions=len(open_positions),
        portfolio_value=portfolio_value,
        starting_cash=starting_cash,
        current_cash=current_cash,
        exit_reasons=dict(Counter(p.close_reason or "unknown" for p in closed)),
        excursions=asdict(compute_excursion_stats(closed)),
    )
