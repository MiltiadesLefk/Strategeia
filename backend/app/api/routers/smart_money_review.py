from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.deps import get_app_settings, get_data_provider, get_llm_provider, get_session, require_auth
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.portfolio.sleeves import SleeveError
from app.schemas.smart_money_review_schemas import (
    ReviewBuyResponse,
    ReviewItemOut,
    ReviewListResponse,
    ReviewSkipRequest,
    SleevesOut,
)
from app.schemas.smart_money_sleeve_report_schemas import SleeveReportResponse
from app.services import smart_money_review_service as service
from app.services.smart_money_sleeve_report_service import build_sleeve_report

router = APIRouter(prefix="/api/smart-money", tags=["smart-money"], dependencies=[Depends(require_auth)])


@router.get("/review", response_model=ReviewListResponse)
def get_review_queue(
    status: str | None = Query(default=None, max_length=24, description="needs_review (default), decided, or one status"),
    limit: int = Query(default=service.DEFAULT_LIMIT, ge=1, le=service.MAX_LIMIT),
    session: Session = Depends(get_session),
) -> ReviewListResponse:
    """The review queue, newest first. Read-only: nothing is evaluated, fetched or written here."""
    return service.list_review(session, status=status, limit=limit)


@router.post("/review/{item_id}/buy", response_model=ReviewBuyResponse)
def buy_review_item(
    item_id: int,
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> ReviewBuyResponse:
    """Buy from the queue: a paper position in the smart-money-manual sleeve, through the engine (session
    gate and caps apply). Market closed: queued for the open, `position_id` null. 409 with the reason when refused."""
    try:
        return service.buy_review_item(session, settings, data_provider, llm_provider, item_id)
    except service.ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SleeveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/review/{item_id}/skip", response_model=ReviewItemOut)
def skip_review_item(
    item_id: int,
    body: ReviewSkipRequest | None = None,
    session: Session = Depends(get_session),
) -> ReviewItemOut:
    try:
        return service.skip_review_item(session, item_id, body.note if body else None)
    except service.ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/sleeves", response_model=SleevesOut)
def create_smart_money_sleeves(session: Session = Depends(get_session)) -> SleevesOut:
    """Explicitly create the smart-money-auto and smart-money-manual paper accounts (idempotent)."""
    try:
        auto, manual, created = service.setup_sleeves(session)
    except SleeveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return SleevesOut(auto_key=auto, manual_key=manual, created=created)


@router.get("/sleeve-report", response_model=SleeveReportResponse)
def get_sleeve_report(session: Session = Depends(get_session)) -> SleeveReportResponse:
    """Per-sleeve review report (NEW_PLAN section 8). Read-only: no writes, no snapshots, no network."""
    return build_sleeve_report(session)
