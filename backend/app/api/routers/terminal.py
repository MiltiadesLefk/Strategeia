from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_data_provider, get_llm_provider, require_auth
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.schemas.terminal_schemas import HeatmapResponse, MacroResponse, RecapResponse
from app.services import market_terminal_service as svc

router = APIRouter(prefix="/api/terminal", tags=["terminal"], dependencies=[Depends(require_auth)])


@router.get("/heatmap", response_model=HeatmapResponse)
def heatmap(
    window: Literal["1d", "5d", "1m"] = Query("1d"),
    limit: int = Query(svc.DEFAULT_SAMPLE_SIZE, ge=1, le=svc.MAX_SAMPLE_SIZE, description="How many universe symbols to read"),
    data_provider: DataProvider = Depends(get_data_provider),
) -> HeatmapResponse:
    return svc.get_heatmap(data_provider, window, limit)


@router.get("/macro", response_model=MacroResponse)
def macro(data_provider: DataProvider = Depends(get_data_provider)) -> MacroResponse:
    return svc.get_macro(data_provider)


@router.get("/recap", response_model=RecapResponse)
def recap(
    ai: bool = Query(False, description="Also write an AI paragraph (only when an LLM provider is configured)"),
    limit: int = Query(svc.DEFAULT_SAMPLE_SIZE, ge=1, le=svc.MAX_SAMPLE_SIZE),
    data_provider: DataProvider = Depends(get_data_provider),
    llm: LLMProvider = Depends(get_llm_provider),
) -> RecapResponse:
    return svc.get_recap(data_provider, llm, ai=ai, limit=limit)
