from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_data_provider, require_auth
from app.analysis.indicators import latest_atr
from app.analysis.trend import analyze_chart
from app.data_providers.base import DataProvider
from app.risk.position_sizing import calculate_position_size, derive_targets
from app.services.trade_plan_service import ATR_PERIOD, ATR_STOP_MULTIPLE
from app.schemas.risk_schemas import RiskCalculateRequest, RiskCalculateResponse

router = APIRouter(prefix="/api/risk", tags=["risk"], dependencies=[Depends(require_auth)])


@router.post("/calculate", response_model=RiskCalculateResponse)
def calculate(
    req: RiskCalculateRequest,
    data_provider: DataProvider = Depends(get_data_provider),
) -> RiskCalculateResponse:
    ohlcv = data_provider.get_ohlcv(req.symbol.upper(), period="1y", interval="1d")
    chart = analyze_chart(ohlcv)

    sizing = calculate_position_size(req.account_size, req.risk_pct, req.entry, req.stop)
    targets = derive_targets(req.entry, req.stop, req.direction, chart.support, chart.resistance)

    # Same ATR floor the auto-generated plans use (trade_plan_service), so a
    # hand-entered stop can be measured against the instrument's real daily
    # range instead of looking fine purely because it sits under a level.
    atr_value = latest_atr(ohlcv, ATR_PERIOD)
    stop_atr_multiple = (abs(req.entry - req.stop) / atr_value) if atr_value else None
    suggested_atr_stop = None
    if atr_value:
        offset = ATR_STOP_MULTIPLE * atr_value
        suggested_atr_stop = req.entry - offset if req.direction == "long" else req.entry + offset

    return RiskCalculateResponse(
        atr=atr_value,
        stop_atr_multiple=stop_atr_multiple,
        suggested_atr_stop=suggested_atr_stop,
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
