from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.api.deps import get_app_settings, get_data_provider, get_session, require_shared_secret
from app.api.routers.portfolio import build_engine
from app.api.routers.trade_plans import trade_plan_to_response
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.universe import get_default_watchlist
from app.portfolio.models import TradePlanRecord
from app.portfolio.stats import compute_portfolio_stats
from app.schemas.dashboard_schemas import DashboardSummary
from app.schemas.portfolio_schemas import PortfolioStatsSchema
from app.services.scanner_service import scan_symbols

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"], dependencies=[Depends(require_shared_secret)])

TOP_SETUPS_LIMIT = 5


@router.get("/summary", response_model=DashboardSummary)
def summary(
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> DashboardSummary:
    watchlist = get_default_watchlist(settings.scan_universe_size)
    scan_results, _errors = scan_symbols(watchlist, data_provider)
    top_setups = sorted(
        (r for r in scan_results if r.signal != "no_signal"), key=lambda r: r.score, reverse=True
    )[:TOP_SETUPS_LIMIT]
    potential_setups = sum(1 for r in scan_results if r.signal == "potential_setup")

    build_engine(session, data_provider, settings).mark_to_market()
    stats = compute_portfolio_stats(session, data_provider, settings.paper_starting_cash)

    latest_plan = session.exec(select(TradePlanRecord).order_by(TradePlanRecord.created_at.desc())).first()

    top_pick_plan = None
    if top_setups:
        top_pick_plan = session.exec(
            select(TradePlanRecord)
            .where(TradePlanRecord.symbol == top_setups[0].symbol)
            .order_by(TradePlanRecord.created_at.desc())
        ).first()

    return DashboardSummary(
        stats=PortfolioStatsSchema(**stats.__dict__),
        markets_scanned=len(watchlist),
        potential_setups=potential_setups,
        top_setups=top_setups,
        latest_trade_plan=trade_plan_to_response(latest_plan) if latest_plan else None,
        top_pick_trade_plan=trade_plan_to_response(top_pick_plan) if top_pick_plan else None,
    )
