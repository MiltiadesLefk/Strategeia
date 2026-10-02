"""Building paper-trading engines for sleeves, and running them all.

The engine itself is scoped to one sleeve (portfolio/engine.py); this module is
the glue every caller shares: one place that turns app settings plus a sleeve
into an engine, and one that sweeps every sleeve's exits (the scheduler and the
read endpoints both need "mark everything").
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.portfolio.engine import Clock, PaperTradingEngine
from app.portfolio.models import PaperPosition, Sleeve
from app.portfolio.sleeves import CORE_SLEEVE_KEY


def build_sleeve_engine(
    session: Session,
    data_provider: DataProvider,
    settings: AppSettings,
    sleeve: Sleeve | None = None,
    *,
    clock: Clock | None = None,
) -> PaperTradingEngine:
    """The engine for `sleeve` (None = core) with the shared strategy settings.
    Only the starting cash differs by sleeve: core follows Settings -> Paper
    Account, any other sleeve is seeded with its own starting cash."""
    is_core = sleeve is None or sleeve.key == CORE_SLEEVE_KEY
    return PaperTradingEngine(
        session,
        data_provider,
        settings.paper_starting_cash if is_core else sleeve.starting_cash,
        settings.max_concurrent_positions,
        slippage_bps=settings.slippage_bps,
        commission_per_trade=settings.commission_per_trade,
        max_positions_per_sector=settings.max_positions_per_sector,
        max_position_pct_of_adv=settings.max_position_pct_of_adv,
        clock=clock,
        max_holding_days=settings.max_holding_days,
        sleeve=None if is_core else sleeve,
    )


def mark_all_sleeves(
    session: Session, data_provider: DataProvider, settings: AppSettings, *, snapshot: bool = True
) -> list[PaperPosition]:
    """Runs the exit scan for every sleeve (core first), each through its own
    engine so each only touches its own positions and cash. A disabled sleeve is
    still swept: switching a sleeve off stops new trades, not the management of
    the ones it holds. Returns every position closed. Reads nothing it does not
    own: the core sleeve row is not created here."""
    sleeves = [None, *session.exec(select(Sleeve).where(Sleeve.key != CORE_SLEEVE_KEY).order_by(Sleeve.id)).all()]
    closed: list[PaperPosition] = []
    for sleeve in sleeves:
        closed.extend(build_sleeve_engine(session, data_provider, settings, sleeve).mark_to_market(snapshot=snapshot))
    return closed
