from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.api.deps import get_app_settings, get_data_provider, get_llm_provider, get_session
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.portfolio.models import TradePlanRecord
from app.schemas.trade_plan_schemas import TradePlanGenerateRequest, TradePlanResponse
from app.services.trade_plan_service import generate_trade_plan

router = APIRouter(prefix="/api/trade-plans", tags=["trade-plans"])


@router.post("/generate", response_model=TradePlanResponse)
def generate(
    req: TradePlanGenerateRequest,
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    settings: AppSettings = Depends(get_app_settings),
    session: Session = Depends(get_session),
) -> TradePlanResponse:
    account_size = req.account_size if req.account_size is not None else settings.paper_starting_cash
    risk_pct = req.risk_pct if req.risk_pct is not None else settings.default_risk_pct
    return generate_trade_plan(req.symbol.upper(), account_size, risk_pct, data_provider, llm_provider, session)


@router.get("", response_model=list[TradePlanResponse])
def list_trade_plans(session: Session = Depends(get_session)) -> list[TradePlanResponse]:
    records = session.exec(select(TradePlanRecord).order_by(TradePlanRecord.created_at.desc())).all()
    return [trade_plan_to_response(r) for r in records]


@router.get("/{plan_id}", response_model=TradePlanResponse)
def get_trade_plan(plan_id: int, session: Session = Depends(get_session)) -> TradePlanResponse:
    record = session.get(TradePlanRecord, plan_id)
    if record is None:
        return TradePlanResponse(symbol="", direction=None, reason="Trade plan not found")
    return trade_plan_to_response(record)


def trade_plan_to_response(record: TradePlanRecord) -> TradePlanResponse:
    return TradePlanResponse(
        id=record.id,
        symbol=record.symbol,
        direction=record.direction,
        entry=record.entry,
        stop=record.stop,
        tp1=record.tp1,
        tp2=record.tp2,
        rr1=record.rr1,
        rr2=record.rr2,
        suggested_shares=record.suggested_shares,
        account_risk_dollars=record.account_risk_dollars,
        potential_gain=record.suggested_shares * abs(record.tp1 - record.entry),
        potential_risk=record.account_risk_dollars,
        confidence_score=record.confidence_score,
        time_horizon=record.time_horizon,
        ai_take_text=record.ai_take_text,
        ai_provider=record.ai_provider,
        status=record.status,
        created_at=record.created_at,
    )
