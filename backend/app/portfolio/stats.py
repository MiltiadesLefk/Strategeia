from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from app.data_providers.base import AllProvidersFailedError, DataProvider
from app.portfolio.models import AccountState, PaperPosition


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


def compute_portfolio_stats(session: Session, data_provider: DataProvider, default_starting_cash: float) -> PortfolioStats:
    account = session.exec(select(AccountState)).first()
    starting_cash = account.starting_cash if account else default_starting_cash
    current_cash = account.current_cash if account else default_starting_cash

    closed = session.exec(select(PaperPosition).where(PaperPosition.status == "closed")).all()
    total_trades = len(closed)
    wins = sum(1 for p in closed if (p.realized_pnl or 0) > 0)
    win_rate = (wins / total_trades * 100) if total_trades else 0.0
    rr_values = [p.realized_r for p in closed if p.realized_r is not None]
    avg_rr = (sum(rr_values) / len(rr_values)) if rr_values else None

    open_positions = session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all()
    mark_value = 0.0
    for position in open_positions:
        try:
            price = data_provider.get_quote(position.symbol).price
        except AllProvidersFailedError:
            price = position.entry_price
        mark_value += position.shares * price

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
    )
