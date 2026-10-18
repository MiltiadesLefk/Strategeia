from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.deps import get_data_provider, get_llm_provider, get_session, require_auth
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.schemas.earnings_preview_schemas import EarningsPreviewResponse
from app.schemas.research_schemas import QualityGateCheck, QualityGateResponse, ResearchResponse
from app.services.earnings_preview_service import build_earnings_preview
from app.services.hold_buy_service import check_quality
from app.services.research_service import get_research

router = APIRouter(prefix="/api/research", tags=["research"], dependencies=[Depends(require_auth)])


@router.get("/{symbol}", response_model=ResearchResponse)
def research(
    symbol: str,
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    session: Session = Depends(get_session),
) -> ResearchResponse:
    # The session is only for the dated archive of the news/fundamentals this
    # request fetches; the response itself doesn't depend on it.
    return get_research(symbol.upper(), data_provider, llm_provider, session)


@router.get("/{symbol}/quality-gate", response_model=QualityGateResponse)
def quality_gate(symbol: str, data_provider: DataProvider = Depends(get_data_provider)) -> QualityGateResponse:
    # Read-only: runs the hold quality gate as the scan would (no session, so nothing is archived or bought).
    try:
        gate = check_quality(data_provider, symbol.upper())
    except Exception as exc:  # noqa: BLE001 - no quote or history = no verdict, said plainly
        raise HTTPException(status_code=502, detail=f"Could not read the data the gate needs: {exc}") from exc
    return QualityGateResponse(
        symbol=symbol.upper(), passed=gate.passed, dip=gate.dip,
        checks=[QualityGateCheck(name=c.name, ok=c.ok, note=c.note) for c in gate.checks],
    )


@router.get("/{symbol}/earnings-preview", response_model=EarningsPreviewResponse)
def earnings_preview(
    symbol: str,
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> EarningsPreviewResponse:
    # Read-only: the facts come from cached provider data, and the finished preview
    # (AI paragraph included) is cached briefly in the service. Nothing is written.
    return build_earnings_preview(symbol.upper(), data_provider, llm_provider)
