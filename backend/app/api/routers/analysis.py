from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_app_settings, get_data_provider, get_llm_provider, require_auth
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.llm_providers.null_provider import NullLLMProvider
from app.schemas.analysis_schemas import AnalysisResponse
from app.services.analysis_service import get_analysis

router = APIRouter(prefix="/api/analysis", tags=["analysis"], dependencies=[Depends(require_auth)])


@router.get("/{symbol}", response_model=AnalysisResponse)
def analysis(
    symbol: str,
    range: str = Query("3mo", pattern="^(1d|1w|1mo|3mo|6mo|1y)$"),
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> AnalysisResponse:
    # Off: the rule-based insight text and no model call (NullLLMProvider is what "none" builds).
    narrator = llm_provider if settings.ai_chart_insight_enabled else NullLLMProvider()
    return get_analysis(symbol.upper(), data_provider, narrator, range_=range)
