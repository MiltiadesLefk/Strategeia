from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analysis import valuation as val
from app.api.deps import get_data_provider, get_llm_provider, require_auth
from app.data_providers.base import DataProvider
from app.data_providers.universe_store import SYMBOL_PATTERN
from app.llm_providers.base import LLMProvider
from app.schemas.valuation_schemas import ValuationResponse
from app.services import valuation_service as svc

router = APIRouter(prefix="/api/valuation", tags=["valuation"], dependencies=[Depends(require_auth)])


@router.get("/{symbol}", response_model=ValuationResponse)
def valuation(
    symbol: str,
    growth: float | None = Query(None, description="Yearly revenue growth, percent; reported history when omitted"),
    margin: float | None = Query(None, description="Net margin, percent; latest reported year when omitted"),
    discount: float = Query(val.DEFAULT_DISCOUNT_PCT, description="Discount rate, percent"),
    terminal: float = Query(val.DEFAULT_TERMINAL_PCT, description="Terminal growth, percent"),
    years: int = Query(val.DEFAULT_YEARS, ge=1, le=val.MAX_YEARS),
    explain: bool = Query(False, description="Add a short AI paragraph (routine model); falls back to plain rule text"),
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> ValuationResponse:
    """Read-only and informational: a discounted-earnings estimate with editable assumptions, plus peer multiples."""
    symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.match(symbol):
        raise HTTPException(status_code=422, detail="Not a valid ticker symbol")
    return svc.get_valuation(
        data_provider,
        symbol,
        growth_pct=growth,
        net_margin_pct=margin,
        discount_rate_pct=discount,
        terminal_growth_pct=terminal,
        years=years,
        llm=llm_provider if explain else None,
    )
