from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.deps import get_app_settings, get_data_provider, get_llm_provider, get_session, require_auth
from app.committee import service
from app.committee.models import CommitteeRun
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.universe_store import SYMBOL_PATTERN
from app.llm_providers.base import LLMProvider
from app.schemas.committee_schemas import (
    CommitteeRunList,
    CommitteeRunSchema,
    CommitteeStartRequest,
)

router = APIRouter(prefix="/api/committee", tags=["committee"], dependencies=[Depends(require_auth)])


@router.post("/runs", response_model=CommitteeRunSchema, status_code=202)
def start_run(
    req: CommitteeStartRequest,
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    llm: LLMProvider = Depends(get_llm_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> CommitteeRunSchema:
    """Start one committee run in the background and return it at once; poll GET /runs/{id} to watch
    each report appear. One run at a time. An opinion only: nothing is opened, sized or stopped."""
    symbol = req.symbol.strip().upper()
    if not SYMBOL_PATTERN.match(symbol):
        raise HTTPException(status_code=422, detail="Not a valid ticker symbol")
    manager = service.get_committee_manager()
    try:
        run_id = manager.start(symbol, data_provider, llm, settings)
    except service.CommitteeNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except service.CommitteeBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    run = session.get(CommitteeRun, run_id)
    assert run is not None
    return service.run_schema(run)


@router.get("/runs", response_model=CommitteeRunList)
def list_runs(
    symbol: str | None = Query(None, max_length=12),
    limit: int = Query(30, ge=1, le=service.MAX_HISTORY),
    session: Session = Depends(get_session),
) -> CommitteeRunList:
    """Saved runs, newest first. Read-only."""
    wanted = symbol.strip().upper() if symbol else None
    return CommitteeRunList(runs=[service.run_summary(r) for r in service.list_runs(session, wanted, limit)])


@router.get("/runs/{run_id}", response_model=CommitteeRunSchema)
def read_run(run_id: int, session: Session = Depends(get_session)) -> CommitteeRunSchema:
    """One run with every report written so far. Read-only, so it is safe to poll."""
    run = session.get(CommitteeRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Committee run not found")
    return service.run_schema(run)
