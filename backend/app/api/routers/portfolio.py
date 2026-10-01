from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import get_app_settings, get_data_provider, get_session, require_auth
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.markets import format_market_time
from app.portfolio.engine import (
    DuplicatePositionError,
    InsufficientCashError,
    MarketClosedError,
    MaxPositionsExceededError,
    PaperTradingEngine,
    SectorConcentrationError,
    StalePlanError,
)
from app.portfolio.models import AccountState, EquitySnapshot, PaperPosition, TradePlanRecord
from app.portfolio.stats import compute_portfolio_stats
from app.schemas.portfolio_schemas import (
    ClosePositionRequest,
    EquityPointSchema,
    OpenPositionRequest,
    PortfolioStatsSchema,
    PositionSchema,
)
from app.services.deferred_evaluation_service import pending_redo_for_plan
from app.strategy.service import plan_strategy_versions

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"], dependencies=[Depends(require_auth)])


def build_engine(session: Session, data_provider: DataProvider, settings: AppSettings) -> PaperTradingEngine:
    return PaperTradingEngine(
        session,
        data_provider,
        settings.paper_starting_cash,
        settings.max_concurrent_positions,
        slippage_bps=settings.slippage_bps,
        commission_per_trade=settings.commission_per_trade,
        max_positions_per_sector=settings.max_positions_per_sector,
        max_position_pct_of_adv=settings.max_position_pct_of_adv,
        max_holding_days=settings.max_holding_days,
    )


def position_to_schema(position: PaperPosition, strategy_versions: dict[int, int | None] | None = None) -> PositionSchema:
    """`strategy_versions` maps plan id -> the plan's strategy version (see
    app/strategy); a position inherits its plan's rather than storing its own."""
    version = (strategy_versions or {}).get(position.trade_plan_id)
    return PositionSchema(**position.model_dump(), strategy_version=version)


@router.get("/positions", response_model=list[PositionSchema])
def list_positions(
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> list[PositionSchema]:
    # snapshot=False: a GET must not append to the equity curve. With
    # react-query's refetch-on-focus, every tab focus used to write an
    # EquitySnapshot row, so the curve was sampled by how often the dashboard
    # was looked at rather than by time — and the table grew without bound.
    # Exit detection still runs here so a stop/TP that fired between
    # scheduler ticks shows up immediately; only the curve write is dropped.
    build_engine(session, data_provider, settings).mark_to_market(snapshot=False)
    positions = session.exec(select(PaperPosition).order_by(PaperPosition.opened_at.desc())).all()
    strategy_versions = plan_strategy_versions(session, [p.trade_plan_id for p in positions])
    return [position_to_schema(p, strategy_versions) for p in positions]


@router.post("/positions", response_model=PositionSchema)
def open_position(
    req: OpenPositionRequest,
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> PositionSchema:
    plan = session.get(TradePlanRecord, req.trade_plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Trade plan not found")
    if plan.status != "pending":
        raise HTTPException(status_code=400, detail=f"Trade plan is already {plan.status}")
    # D10 = C: a plan made while the market was closed is replaced by a fresh
    # one shortly after the open, and only the fresh one may execute — not
    # this one, built on the previous session's prices, even in the minutes
    # between the bell and its redo.
    redo = pending_redo_for_plan(session, plan.id)
    if redo is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This plan was made while the market was closed. It will be redone from fresh data at "
                f"{format_market_time(redo.due_at)}, and only the fresh plan can be executed."
            ),
        )
    try:
        position = build_engine(session, data_provider, settings).open_position(plan)
    except MarketClosedError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{exc} Plans made while the market is closed are redone from fresh data at the next open.",
        ) from exc
    except (
        InsufficientCashError,
        DuplicatePositionError,
        MaxPositionsExceededError,
        SectorConcentrationError,
        StalePlanError,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return position_to_schema(position, plan_strategy_versions(session, [position.trade_plan_id]))


@router.post("/positions/{position_id}/close", response_model=PositionSchema)
def close_position(
    position_id: int,
    req: ClosePositionRequest,
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> PositionSchema:
    position = session.get(PaperPosition, position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    if position.status != "open":
        raise HTTPException(status_code=400, detail="Position is already closed")
    price = data_provider.get_quote(position.symbol).price
    closed = build_engine(session, data_provider, settings).close_position(position, price, req.reason)
    return position_to_schema(closed, plan_strategy_versions(session, [closed.trade_plan_id]))


@router.get("/stats", response_model=PortfolioStatsSchema)
def stats(
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> PortfolioStatsSchema:
    build_engine(session, data_provider, settings).mark_to_market(snapshot=False)  # read-only: see list_positions
    result = compute_portfolio_stats(session, data_provider, settings.paper_starting_cash)
    return PortfolioStatsSchema(**result.__dict__)


@router.get("/equity-curve", response_model=list[EquityPointSchema])
def equity_curve(session: Session = Depends(get_session)) -> list[EquityPointSchema]:
    snapshots = session.exec(select(EquitySnapshot).order_by(EquitySnapshot.timestamp.asc())).all()
    return [EquityPointSchema(**s.model_dump()) for s in snapshots]


@router.post("/reset", response_model=PortfolioStatsSchema)
def reset_portfolio(
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> PortfolioStatsSchema:
    """Wipes the simulated portfolio (positions, equity history, cash
    balance) and recreates the account fresh at the current Settings ->
    Paper Account -> Starting Cash value. Generated trade-plan history is
    left alone — this only resets the paper-trading side, not the AI
    scan/plan record. Needed because `AccountState` is only ever seeded once
    (see SettingsPage's "only takes effect for a fresh account" note) — this
    is the supported way to actually apply a changed starting-cash value."""
    for position in session.exec(select(PaperPosition)).all():
        session.delete(position)
    for snapshot in session.exec(select(EquitySnapshot)).all():
        session.delete(snapshot)
    for account in session.exec(select(AccountState)).all():
        session.delete(account)
    for plan in session.exec(select(TradePlanRecord).where(TradePlanRecord.status == "executed")).all():
        plan.status = "discarded"
        session.add(plan)
    session.commit()

    build_engine(session, data_provider, settings).get_account_state()
    result = compute_portfolio_stats(session, data_provider, settings.paper_starting_cash)
    return PortfolioStatsSchema(**result.__dict__)
