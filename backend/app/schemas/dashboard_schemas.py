from __future__ import annotations

from pydantic import BaseModel

from app.schemas.portfolio_schemas import PortfolioStatsSchema
from app.schemas.scan_schemas import ScanResultSchema
from app.schemas.trade_plan_schemas import TradePlanResponse


class DashboardSummary(BaseModel):
    stats: PortfolioStatsSchema
    markets_scanned: int
    potential_setups: int
    top_setups: list[ScanResultSchema]
    latest_trade_plan: TradePlanResponse | None
    # Deliberately separate from `latest_trade_plan` (which can be for *any*
    # symbol, whenever it was last generated): this is scoped to
    # `top_setups[0]` specifically, so the "AI Insights" card's Technical/AI
    # Take/Risk blocks always narrate the SAME symbol instead of silently
    # Frankensteining today's top pick with an unrelated stale plan.
    top_pick_trade_plan: TradePlanResponse | None
