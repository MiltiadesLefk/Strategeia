from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.analysis.fundamental_scoring import (
    FUNDAMENTAL_SCORE_CAP,
    NEWS_SCORE_CAP,
    score_fundamentals,
    score_news_sentiment,
)
from app.analysis.insight_text import trade_plan_take_text
from app.analysis.scanner_scoring import score_symbol
from app.analysis.trend import analyze_chart
from app.config import load_app_settings
from app.data_providers.base import AllProvidersFailedError, CompanyOverview, DataProvider, FinancialYear, NewsItem
from app.llm_providers.base import LLMProvider
from app.llm_providers.factory import generate_with_fallback
from app.llm_providers.prompts import build_trade_plan_take_prompt
from app.portfolio.engine import DuplicatePositionError, InsufficientCashError, PaperTradingEngine
from app.portfolio.models import TradePlanRecord
from app.risk.position_sizing import calculate_position_size, derive_targets
from app.schemas.trade_plan_schemas import TradePlanResponse
from app.services.telegram_service import notify_trade_plan

logger = logging.getLogger(__name__)

STOP_BUFFER_PCT = 0.01
FALLBACK_STOP_PCT = 0.03
TECHNICAL_SCORE_CAP = 6
MAX_SCORE_FOR_CONFIDENCE = TECHNICAL_SCORE_CAP + FUNDAMENTAL_SCORE_CAP + NEWS_SCORE_CAP
CONFIDENCE_FLOOR = 20
CONFIDENCE_CEILING = 90


def _derive_entry_and_stop(direction: str, price: float, support: list[float], resistance: list[float]) -> tuple[float, float]:
    entry = price
    if direction == "long":
        stop = support[0] * (1 - STOP_BUFFER_PCT) if support else entry * (1 - FALLBACK_STOP_PCT)
    else:
        stop = resistance[0] * (1 + STOP_BUFFER_PCT) if resistance else entry * (1 + FALLBACK_STOP_PCT)
    return entry, stop


def _confidence_score(score: int) -> int:
    clamped = max(0, min(MAX_SCORE_FOR_CONFIDENCE, score))
    span = CONFIDENCE_CEILING - CONFIDENCE_FLOOR
    return round(CONFIDENCE_FLOOR + (clamped / MAX_SCORE_FOR_CONFIDENCE) * span)


def _fetch_fundamentals_and_news(
    symbol: str, data_provider: DataProvider
) -> tuple[CompanyOverview | None, list[FinancialYear], list[NewsItem]]:
    """Best-effort: a symbol with no fundamentals/news coverage (e.g. a
    crypto pair) just contributes a 0 fundamental/news score rather than
    blocking trade-plan generation — same graceful-degradation pattern as
    research_service.get_research."""
    try:
        overview = data_provider.get_company_overview(symbol)
    except AllProvidersFailedError:
        overview = None
    try:
        financial_years = data_provider.get_financials(symbol).years
    except AllProvidersFailedError:
        financial_years = []
    try:
        news = data_provider.get_news(symbol, limit=5)
    except AllProvidersFailedError:
        news = []
    return overview, financial_years, news


def generate_trade_plan(
    symbol: str,
    account_size: float,
    risk_pct: float,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    session: Session,
) -> TradePlanResponse:
    ohlcv = data_provider.get_ohlcv(symbol, period="6mo", interval="1d")
    quote = data_provider.get_quote(symbol)
    chart = analyze_chart(ohlcv)

    if chart.trend == "Neutral":
        return TradePlanResponse(symbol=symbol, direction=None, reason="No clear trend")

    direction = "long" if chart.trend == "Bullish" else "short"
    entry, stop = _derive_entry_and_stop(direction, chart.price, chart.support, chart.resistance)

    sizing = calculate_position_size(account_size, risk_pct, entry, stop)
    targets = derive_targets(entry, stop, direction, chart.support, chart.resistance)

    volume_ratio = quote.volume / quote.avg_volume_20d if quote.avg_volume_20d > 0 else 1.0
    scan_result = score_symbol(symbol, quote.price, quote.change_pct_24h, chart, volume_ratio)

    # "Smart" layer: fundamentals + news sentiment, on top of the pure
    # technical scanner score — see analysis/fundamental_scoring.py. Every
    # symbol (including crypto pairs with no fundamentals/news coverage)
    # still gets a plan; those signals just contribute 0 in that case.
    overview, financial_years, news = _fetch_fundamentals_and_news(symbol, data_provider)
    earnings_date = data_provider.get_earnings_date(symbol)
    news_score, news_reasons = score_news_sentiment(news)
    fundamental_score, fundamental_reasons = (
        score_fundamentals(overview, financial_years, earnings_date, quote.price) if overview else (0, [])
    )
    combined_score = scan_result.score + fundamental_score + news_score
    confidence_score = _confidence_score(combined_score)
    technical_reason = f"{chart.trend} trend with {chart.momentum.lower()} momentum"
    signal_reasons = "; ".join([technical_reason, *fundamental_reasons, *news_reasons])

    fallback_text = trade_plan_take_text(symbol, direction, targets.rr1, confidence_score)
    prompt = build_trade_plan_take_prompt(
        symbol, direction, entry, stop, targets.tp1, targets.rr1, confidence_score, chart, volume_ratio,
        signal_reasons=fundamental_reasons + news_reasons,
    )
    llm_result = generate_with_fallback(llm_provider, prompt, fallback_text)

    # Regenerating for a symbol that already has a pending (not yet executed)
    # plan superseded it rather than piling up duplicate rows in history —
    # the old numbers are stale the moment a fresh one is computed, whether
    # they differ or (as when nothing moved) come out identical.
    stale_pending = session.exec(
        select(TradePlanRecord).where(TradePlanRecord.symbol == symbol, TradePlanRecord.status == "pending")
    ).all()
    for stale in stale_pending:
        stale.status = "discarded"
        session.add(stale)

    record = TradePlanRecord(
        symbol=symbol,
        direction=direction,
        entry=entry,
        stop=stop,
        tp1=targets.tp1,
        tp2=targets.tp2,
        rr1=targets.rr1,
        rr2=targets.rr2,
        suggested_shares=sizing.shares,
        account_risk_dollars=sizing.account_risk_dollars,
        confidence_score=confidence_score,
        ai_take_text=llm_result.text,
        ai_provider=llm_result.provider,
        technical_score=scan_result.score,
        fundamental_score=fundamental_score,
        news_score=news_score,
        signal_reasons=signal_reasons,
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    settings = load_app_settings()

    auto_execute_note = ""
    if settings.auto_execute_trade_plans:
        try:
            PaperTradingEngine(session, data_provider, account_size).open_position(record)
            session.refresh(record)
            auto_execute_note = "\nAuto-executed as a paper position."
        except (InsufficientCashError, DuplicatePositionError) as exc:
            logger.warning("Auto-execute skipped for %s: %s", symbol, exc)
            auto_execute_note = f"\nAuto-execute skipped: {exc}"

    notify_text = (
        f"New trade plan: {symbol} {direction.upper()}\n"
        f"Entry: ${entry:.2f}  Stop: ${stop:.2f}  TP1: ${targets.tp1:.2f}\n"
        f"Confidence: {confidence_score}%{auto_execute_note}"
    )
    notify_trade_plan(settings.telegram_bot_token, settings.telegram_chat_id, notify_text)

    return TradePlanResponse(
        id=record.id,
        symbol=symbol,
        direction=direction,
        entry=entry,
        stop=stop,
        tp1=targets.tp1,
        tp2=targets.tp2,
        rr1=targets.rr1,
        rr2=targets.rr2,
        suggested_shares=sizing.shares,
        account_risk_dollars=sizing.account_risk_dollars,
        potential_gain=sizing.shares * abs(targets.tp1 - entry),
        potential_risk=sizing.account_risk_dollars,
        confidence_score=confidence_score,
        time_horizon=record.time_horizon,
        ai_take_text=llm_result.text,
        ai_provider=llm_result.provider,
        status=record.status,
        created_at=record.created_at,
        technical_score=scan_result.score,
        fundamental_score=fundamental_score,
        news_score=news_score,
        signal_reasons=signal_reasons,
    )
