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
