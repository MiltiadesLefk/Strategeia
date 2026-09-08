from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import get_app_settings, get_data_provider, get_session
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.portfolio.engine import DuplicatePositionError, InsufficientCashError, PaperTradingEngine
from app.portfolio.models import AccountState, EquitySnapshot, PaperPosition, TradePlanRecord
from app.portfolio.stats import compute_portfolio_stats
from app.schemas.portfolio_schemas import (
    ClosePositionRequest,
    EquityPointSchema,
    OpenPositionRequest,
    PortfolioStatsSchema,
    PositionSchema,
)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def build_engine(session: Session, data_provider: DataProvider, settings: AppSettings) -> PaperTradingEngine:
    return PaperTradingEngine(session, data_provider, settings.paper_starting_cash)


def position_to_schema(position: PaperPosition) -> PositionSchema:
    return PositionSchema(**position.model_dump())


@router.get("/positions", response_model=list[PositionSchema])
def list_positions(
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> list[PositionSchema]:
    build_engine(session, data_provider, settings).mark_to_market()
    positions = session.exec(select(PaperPosition).order_by(PaperPosition.opened_at.desc())).all()
    return [position_to_schema(p) for p in positions]


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
    try:
        position = build_engine(session, data_provider, settings).open_position(plan)
    except (InsufficientCashError, DuplicatePositionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return position_to_schema(position)


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
    return position_to_schema(closed)


@router.get("/stats", response_model=PortfolioStatsSchema)
def stats(
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> PortfolioStatsSchema:
    build_engine(session, data_provider, settings).mark_to_market()
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
