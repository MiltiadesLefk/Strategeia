from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.api.deps import get_app_settings, get_data_provider, get_llm_provider, get_session, require_shared_secret
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.portfolio.models import TradePlanRecord
from app.schemas.trade_plan_schemas import TradePlanGenerateRequest, TradePlanResponse
from app.services.trade_plan_service import generate_trade_plan

router = APIRouter(prefix="/api/trade-plans", tags=["trade-plans"], dependencies=[Depends(require_shared_secret)])


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
    # No-trade records (status="no_trade") have no entry/tp1/suggested_shares
    # — nothing to size a potential gain/risk against.
    has_sizing = record.suggested_shares is not None and record.tp1 is not None and record.entry is not None
    return TradePlanResponse(
        id=record.id,
        symbol=record.symbol,
        direction=record.direction,
        reason=record.reason,
        entry=record.entry,
        stop=record.stop,
        tp1=record.tp1,
        tp2=record.tp2,
        rr1=record.rr1,
        rr2=record.rr2,
        suggested_shares=record.suggested_shares,
        account_risk_dollars=record.account_risk_dollars,
        potential_gain=record.suggested_shares * abs(record.tp1 - record.entry) if has_sizing else None,
        potential_risk=record.account_risk_dollars,
        confidence_score=record.confidence_score,
        time_horizon=record.time_horizon,
        ai_take_text=record.ai_take_text,
        ai_provider=record.ai_provider,
        status=record.status,
        created_at=record.created_at,
        technical_score=record.technical_score,
        fundamental_score=record.fundamental_score,
        news_score=record.news_score,
        market_confirmation_score=record.market_confirmation_score,
        vix_regime_score=record.vix_regime_score,
        options_score=record.options_score,
        insider_score=record.insider_score,
        expected_move_score=record.expected_move_score,
        earnings_surprise_score=record.earnings_surprise_score,
        macro_event_score=record.macro_event_score,
        signal_reasons=record.signal_reasons,
        ai_opinion_stance=record.ai_opinion_stance,
        ai_opinion_score=record.ai_opinion_score,
        ai_opinion_text=record.ai_opinion_text,
        ai_news_assessment=record.ai_news_assessment,
    )
