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
from app.portfolio.sleeves import CORE_SLEEVE_KEY, DEFAULT_SLEEVE_STARTING_CASH, SLEEVE_COLORS, SleeveError, list_sleeves
from app.smartmoney.pipeline_rules import (
    AUTO_SLEEVE_KEY,
    AUTO_SLEEVE_NAME,
    MANUAL_SLEEVE_KEY,
    MANUAL_SLEEVE_NAME,
    SMART_MONEY_EXIT_SCAN_PERIOD,
    SMART_MONEY_SLEEVE_KEYS,
    SMART_MONEY_STYLE,
)


def sleeve_position_cap(settings: AppSettings, sleeve: Sleeve | None) -> int:
    """The open-position cap of `sleeve`: the smart-money sleeves have their own
    (`smart_money_max_open_positions`), every other sleeve shares `max_concurrent_positions`."""
    if sleeve is not None and sleeve.key in SMART_MONEY_SLEEVE_KEYS:
        return settings.smart_money_max_open_positions
    return settings.max_concurrent_positions


def build_sleeve_engine(
    session: Session,
    data_provider: DataProvider,
    settings: AppSettings,
    sleeve: Sleeve | None = None,
    *,
    clock: Clock | None = None,
    hold: bool = False,
) -> PaperTradingEngine:
    """The engine for `sleeve` (None = core) with the shared strategy settings.
    Only the starting cash differs by sleeve: core follows Settings -> Paper
    Account, any other sleeve is seeded with its own starting cash."""
    is_core = sleeve is None or sleeve.key == CORE_SLEEVE_KEY
    is_smart_money = sleeve is not None and sleeve.key in SMART_MONEY_SLEEVE_KEYS
    return PaperTradingEngine(
        session,
        data_provider,
        settings.paper_starting_cash if is_core else sleeve.starting_cash,
        settings.hold_max_positions if hold else sleeve_position_cap(settings, sleeve),
        slippage_bps=settings.slippage_bps,
        commission_per_trade=settings.commission_per_trade,
        max_positions_per_sector=settings.hold_max_per_sector if hold else settings.max_positions_per_sector,
        max_position_pct_of_adv=settings.max_position_pct_of_adv,
        clock=clock,
        liquidity_slippage_coefficient=settings.liquidity_slippage_coefficient if settings.liquidity_slippage_enabled else None,
        sleeve=None if is_core else sleeve,
        hold_stop_mode=settings.hold_stop_mode,
        # The smart-money sleeves hold longer, so they read a longer bar window.
        **({"exit_scan_period": SMART_MONEY_EXIT_SCAN_PERIOD} if is_smart_money else {}),
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


def check_touch_stops_all_sleeves(session: Session, data_provider: DataProvider, settings: AppSettings) -> list[PaperPosition]:
    """The live touch-stop check for every sleeve, each through its own engine (a no-op in close mode)."""
    sleeves = [None, *session.exec(select(Sleeve).where(Sleeve.key != CORE_SLEEVE_KEY).order_by(Sleeve.id)).all()]
    closed: list[PaperPosition] = []
    for sleeve in sleeves:
        closed.extend(build_sleeve_engine(session, data_provider, settings, sleeve).check_touch_stops())
    return closed


def ensure_smart_money_sleeves(session: Session) -> tuple[Sleeve, Sleeve]:
    """The `smart-money-auto` and `smart-money-manual` sleeves, created if missing. An explicit
    write (the pipeline's first evaluation, a review Buy, or POST /api/smart-money/sleeves): a GET
    must never call this. Each is its own paper account with its own starting cash; the combined
    equity of the three sleeves is never shown (see notes/Decisions.md)."""
    out: list[Sleeve] = []
    for key, name, note in (
        (AUTO_SLEEVE_KEY, AUTO_SLEEVE_NAME, "Tracker candidates the full evaluation rated tradeable: bought automatically."),
        (MANUAL_SLEEVE_KEY, MANUAL_SLEEVE_NAME, "Tracker candidates the evaluation rated badly that you chose to buy from the review queue."),
    ):
        sleeve = session.exec(select(Sleeve).where(Sleeve.key == key)).first()
        if sleeve is None:
            existing = list_sleeves(session)
            if any(s.name.lower() == name.lower() for s in existing):
                raise SleeveError(f"A sleeve named '{name}' already exists with another key", 409)
            sleeve = Sleeve(
                key=key,
                name=name,
                style=SMART_MONEY_STYLE,
                starting_cash=DEFAULT_SLEEVE_STARTING_CASH,
                color=SLEEVE_COLORS[len(existing) % len(SLEEVE_COLORS)],
                notes=note,
            )
            session.add(sleeve)
            session.commit()
            session.refresh(sleeve)
        out.append(sleeve)
    return out[0], out[1]
