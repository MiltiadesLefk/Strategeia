from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_data_provider, get_llm_provider, require_shared_secret
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.schemas.research_schemas import ResearchResponse
from app.services.research_service import get_research

router = APIRouter(prefix="/api/research", tags=["research"], dependencies=[Depends(require_shared_secret)])


@router.get("/{symbol}", response_model=ResearchResponse)
def research(
    symbol: str,
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> ResearchResponse:
    return get_research(symbol.upper(), data_provider, llm_provider)
