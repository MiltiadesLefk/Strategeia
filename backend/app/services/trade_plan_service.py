from __future__ import annotations

from sqlmodel import Session

from app.analysis.insight_text import trade_plan_take_text
from app.analysis.scanner_scoring import score_symbol
from app.analysis.trend import analyze_chart
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.llm_providers.factory import generate_with_fallback
from app.llm_providers.prompts import build_trade_plan_take_prompt
from app.portfolio.models import TradePlanRecord
from app.risk.position_sizing import calculate_position_size, derive_targets
from app.schemas.trade_plan_schemas import TradePlanResponse

STOP_BUFFER_PCT = 0.01
FALLBACK_STOP_PCT = 0.03
MAX_SCORE_FOR_CONFIDENCE = 6
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
    confidence_score = _confidence_score(scan_result.score)

    fallback_text = trade_plan_take_text(symbol, direction, targets.rr1, confidence_score)
    prompt = build_trade_plan_take_prompt(symbol, direction, entry, stop, targets.tp1, targets.rr1, confidence_score)
    llm_result = generate_with_fallback(llm_provider, prompt, fallback_text)

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
    )
    session.add(record)
    session.commit()
    session.refresh(record)

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
    )
