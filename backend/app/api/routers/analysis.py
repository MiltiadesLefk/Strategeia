from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_data_provider, get_llm_provider
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.schemas.analysis_schemas import AnalysisResponse
from app.services.analysis_service import get_analysis

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get("/{symbol}", response_model=AnalysisResponse)
def analysis(
    symbol: str,
    range: str = Query("3mo", pattern="^(1d|1w|1mo|3mo|6mo|1y)$"),
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> AnalysisResponse:
    return get_analysis(symbol.upper(), data_provider, llm_provider, range_=range)
