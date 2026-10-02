from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import get_app_settings, get_data_provider, get_llm_provider, get_session, require_auth
from app.analysis.shadow_signals import shadow_signals_from_json
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.portfolio.models import Sleeve, TradePlanRecord
from app.portfolio.sleeves import CORE_SLEEVE_KEY, SleeveError, get_sleeve
from app.schemas.trade_plan_schemas import TradePlanGenerateRequest, TradePlanResponse
from app.services.deferred_evaluation_service import pending_redo_for_plan, pending_redo_times
from app.services.trade_plan_service import MAX_SCORE_FOR_CONFIDENCE, clamp_points, generate_trade_plan

router = APIRouter(prefix="/api/trade-plans", tags=["trade-plans"], dependencies=[Depends(require_auth)])


@router.post("/generate", response_model=TradePlanResponse)
def generate(
    req: TradePlanGenerateRequest,
    data_provider: DataProvider = Depends(get_data_provider),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    settings: AppSettings = Depends(get_app_settings),
    session: Session = Depends(get_session),
) -> TradePlanResponse:
    try:
        sleeve = get_sleeve(session, req.sleeve)
    except SleeveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    default_size = settings.paper_starting_cash if sleeve.key == CORE_SLEEVE_KEY else sleeve.starting_cash
    account_size = req.account_size if req.account_size is not None else default_size
    risk_pct = req.risk_pct if req.risk_pct is not None else settings.default_risk_pct
    response = generate_trade_plan(
        req.symbol.upper(), account_size, risk_pct, data_provider, llm_provider, session, sleeve=sleeve
    )
    response.sleeve_key = sleeve.key
    return response


@router.get("", response_model=list[TradePlanResponse])
def list_trade_plans(session: Session = Depends(get_session)) -> list[TradePlanResponse]:
    records = session.exec(select(TradePlanRecord).order_by(TradePlanRecord.created_at.desc())).all()
    redo_times = pending_redo_times(session)
    keys = _sleeve_keys(session)
    return [trade_plan_to_response(r, redo_at=redo_times.get(r.id), sleeve_keys=keys) for r in records]


@router.get("/{plan_id}", response_model=TradePlanResponse)
def get_trade_plan(plan_id: int, session: Session = Depends(get_session)) -> TradePlanResponse:
    record = session.get(TradePlanRecord, plan_id)
    if record is None:
        return TradePlanResponse(symbol="", direction=None, reason="Trade plan not found")
    redo = pending_redo_for_plan(session, record.id)
    return trade_plan_to_response(record, redo_at=redo.due_at if redo else None, sleeve_keys=_sleeve_keys(session))


def _sleeve_keys(session: Session) -> dict[int | None, str]:
    """sleeve_id -> key for labelling plans; a plan with no sleeve is core's."""
    keys: dict[int | None, str] = {None: CORE_SLEEVE_KEY}
    for sleeve in session.exec(select(Sleeve)).all():
        keys[sleeve.id] = sleeve.key
    return keys


def trade_plan_to_response(
    record: TradePlanRecord, *, redo_at: datetime | None = None, sleeve_keys: dict[int | None, str] | None = None
) -> TradePlanResponse:
    """`redo_at` comes from the off-hours queue, not the row itself; callers
    that never show an Execute button (the Dashboard) can leave it out."""
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
        ai_overlay_score=record.ai_overlay_score,
        ai_trade_verdict=record.ai_trade_verdict,
        ai_decision_model=record.ai_decision_model,
        ai_opinion_parse=record.ai_opinion_parse,
        ai_grounding_warnings=record.ai_grounding_warnings,
        # Recomputed from the stored dimensions rather than persisted: it is
        # a pure function of columns already on the row, so a stored copy
        # could only ever drift out of step with them.
        confidence_points=clamp_points(
            sum(
                score or 0
                for score in (
                    record.technical_score, record.fundamental_score, record.news_score,
                    record.market_confirmation_score, record.vix_regime_score, record.options_score,
                    record.insider_score, record.expected_move_score, record.earnings_surprise_score,
                    record.macro_event_score, record.ai_overlay_score,
                )
            )
        ),
        confidence_points_max=MAX_SCORE_FOR_CONFIDENCE,
        auto_execute_note=record.auto_execute_note,
        redo_at=redo_at,
        signal_reasons=record.signal_reasons,
        ai_opinion_stance=record.ai_opinion_stance,
        ai_opinion_score=record.ai_opinion_score,
        ai_opinion_text=record.ai_opinion_text,
        ai_news_assessment=record.ai_news_assessment,
        strategy_version=record.strategy_version,
        sleeve_id=record.sleeve_id,
        sleeve_key=(sleeve_keys or {}).get(record.sleeve_id, CORE_SLEEVE_KEY if record.sleeve_id is None else None),
        shadow_signals=shadow_signals_from_json(record.shadow_signals),
    )
