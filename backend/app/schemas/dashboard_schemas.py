from __future__ import annotations

from pydantic import BaseModel

from app.schemas.portfolio_schemas import PortfolioStatsSchema
from app.schemas.scan_schemas import ScanResultSchema


class DashboardSummary(BaseModel):
    stats: PortfolioStatsSchema
    markets_scanned: int
    potential_setups: int
    top_setups: list[ScanResultSchema]
