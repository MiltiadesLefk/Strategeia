from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.api.deps import get_app_settings, get_data_provider, get_session, require_auth
from app.api.routers.portfolio import _mark_for_read
from app.analysis.live_evidence import extra_notes_from_json, extra_scores_from_json
from app.analysis.confidence import MAX_SCORE_FOR_CONFIDENCE, clamp_points
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.universe import get_default_watchlist
from app.portfolio.models import TradePlanRecord
from app.portfolio.sleeves import CORE_SLEEVE_KEY, SleeveError
from app.portfolio.stats import compute_portfolio_stats
from app.schemas.dashboard_schemas import DashboardSummary
from app.schemas.portfolio_schemas import PortfolioStatsSchema
from app.schemas.trade_plan_schemas import TradePlanResponse
from app.services.scanner_service import scan_symbols

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"], dependencies=[Depends(require_auth)])

TOP_SETUPS_LIMIT = 5


@router.get("/summary", response_model=DashboardSummary)
def summary(
    sleeve: str | None = Query(default=None, description="Sleeve key for the stat cards; omitted = core"),
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> DashboardSummary:
    watchlist = get_default_watchlist(settings.scan_universe_size)
    scan_results, _errors = scan_symbols(watchlist, data_provider)
    top_setups = sorted(
        (r for r in scan_results if r.signal != "no_signal"), key=lambda r: r.score, reverse=True
    )[:TOP_SETUPS_LIMIT]
    potential_setups = sum(1 for r in scan_results if r.signal == "potential_setup")

    # snapshot=False: read-only, same rule as GET /api/portfolio/positions and
    # /stats (see portfolio.list_positions). The exit check still runs so the
    # stats below reflect a stop/TP1 hit since the last scheduled pass; only
    # the equity-curve write is dropped. With the default snapshot=True, every
    # Dashboard load appended a point, so the curve recorded page views.
    _mark_for_read(session, data_provider, settings, sleeve)
    try:
        stats = compute_portfolio_stats(session, data_provider, settings.paper_starting_cash, sleeve)
    except SleeveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    latest_plan = session.exec(select(TradePlanRecord).order_by(TradePlanRecord.created_at.desc())).first()

    top_pick_plan = None
    if top_setups:
        top_pick_plan = session.exec(
            select(TradePlanRecord)
            .where(TradePlanRecord.symbol == top_setups[0].symbol)
            .order_by(TradePlanRecord.created_at.desc())
        ).first()

    return DashboardSummary(
        stats=PortfolioStatsSchema(**stats.__dict__),
        markets_scanned=len(watchlist),
        potential_setups=potential_setups,
        top_setups=top_setups,
        latest_trade_plan=trade_plan_to_response(latest_plan) if latest_plan else None,
        top_pick_trade_plan=trade_plan_to_response(top_pick_plan) if top_pick_plan else None,
    )


def trade_plan_to_response(record: TradePlanRecord) -> TradePlanResponse:
    """Old swing plan rows still show on the Dashboard; nothing writes new ones any more."""
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
        extra_scores=extra_scores_from_json(record.extra_scores),
        extra_notes=extra_notes_from_json(record.extra_scores),
        committee_run_id=record.committee_run_id,
        committee_rating=record.committee_rating,
        committee_note=record.committee_note,
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
            + sum((extra_scores_from_json(record.extra_scores) or {}).values())
        ),
        confidence_points_max=MAX_SCORE_FOR_CONFIDENCE,
        auto_execute_note=record.auto_execute_note,
        signal_reasons=record.signal_reasons,
        ai_opinion_stance=record.ai_opinion_stance,
        ai_opinion_score=record.ai_opinion_score,
        ai_opinion_text=record.ai_opinion_text,
        ai_news_assessment=record.ai_news_assessment,
        strategy_version=record.strategy_version,
        sleeve_id=record.sleeve_id,
        sleeve_key=CORE_SLEEVE_KEY if record.sleeve_id is None else None,
    )
