from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_data_provider, require_shared_secret
from app.analysis.trend import analyze_chart
from app.data_providers.base import DataProvider
from app.risk.position_sizing import calculate_position_size, derive_targets
from app.schemas.risk_schemas import RiskCalculateRequest, RiskCalculateResponse

router = APIRouter(prefix="/api/risk", tags=["risk"], dependencies=[Depends(require_shared_secret)])


@router.post("/calculate", response_model=RiskCalculateResponse)
def calculate(
    req: RiskCalculateRequest,
    data_provider: DataProvider = Depends(get_data_provider),
) -> RiskCalculateResponse:
    ohlcv = data_provider.get_ohlcv(req.symbol.upper(), period="1y", interval="1d")
    chart = analyze_chart(ohlcv)

    sizing = calculate_position_size(req.account_size, req.risk_pct, req.entry, req.stop)
    targets = derive_targets(req.entry, req.stop, req.direction, chart.support, chart.resistance)

    return RiskCalculateResponse(
        shares=sizing.shares,
        risk_per_share=sizing.risk_per_share,
        account_risk_dollars=sizing.account_risk_dollars,
        position_value=sizing.position_value,
        capped_by_cash=sizing.capped_by_cash,
        tp1=targets.tp1,
        tp2=targets.tp2,
        rr1=targets.rr1,
        rr2=targets.rr2,
        potential_gain=sizing.shares * abs(targets.tp1 - req.entry),
        potential_risk=sizing.account_risk_dollars,
    )
