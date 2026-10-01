from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime


class TradePlanGenerateRequest(BaseModel):
    symbol: str
    account_size: float | None = Field(default=None, gt=0)
    risk_pct: float | None = Field(default=None, gt=0, le=100)


class TradePlanResponse(BaseModel):
    id: int | None = None
    symbol: str
    direction: str | None
    reason: str | None = None
    entry: float | None = None
    stop: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    rr1: float | None = None
    rr2: float | None = None
    suggested_shares: int | None = None
    account_risk_dollars: float | None = None
    potential_gain: float | None = None
    potential_risk: float | None = None
    # True when the share count was cut to what the account can actually
    # fund, so the UI can say so instead of showing an unreachable size.
    capped_by_cash: bool | None = None
    # ATR14 and how many ATRs the stop sits from entry — a 0.4-ATR stop is
    # inside the instrument's daily noise and will be taken out by nothing.
    atr: float | None = None
    stop_atr_multiple: float | None = None
    confidence_score: int | None = None
    # The raw evidence points behind confidence_score, and the maximum the
    # engine can award. Confidence is quantised — 17 reachable values, 6.25
    # apart — so "7 / 16" is what the percentage actually means.
    confidence_points: int | None = None
    confidence_points_max: int | None = None
    time_horizon: str | None = None
    ai_take_text: str | None = None
    ai_provider: str | None = None
    status: str | None = None
    created_at: UtcDatetime | None = None
    technical_score: int | None = None
    fundamental_score: int | None = None
    news_score: int | None = None
    market_confirmation_score: int | None = None
    vix_regime_score: int | None = None
    options_score: int | None = None
    insider_score: int | None = None
    expected_move_score: int | None = None
    earnings_surprise_score: int | None = None
    macro_event_score: int | None = None
    # 0 or negative — what the AI Trading Overlay's disagreement cost
    # confidence_score. Distinct from ai_opinion_score below, which is the
    # AI's own conviction in its own stance.
    ai_overlay_score: int | None = None
    # "take" | "pass" — would the AI take this trade? Distinct from
    # ai_opinion_stance (its directional read) and the field acted on.
    ai_trade_verdict: str | None = None
    # The model that gave that verdict (the overlay's decision tier). None when
    # the overlay did not run.
    ai_decision_model: str | None = None
    # "structured" | "lenient" | "failed": how the overlay's reply was read.
    # "failed" means it answered but nothing usable could be read from it.
    ai_opinion_parse: str | None = None
    # Newline-separated figures the overlay quoted that were not in the data it
    # was given. Display only; None when there were none (or no overlay).
    ai_grounding_warnings: str | None = None
    # Plain-English outcome of the auto-execute step (executed / skipped-with-
    # reason / held for manual review on overlay disagreement / deferred
    # because the market was closed). Null on a no_trade record — auto-execute
    # is never attempted there.
    auto_execute_note: str | None = None
    # Set while this plan, made with its market closed, waits to be redone
    # from fresh data at the next open (D10 = C): when that redo is due. Such
    # a plan can't be executed by hand — only the fresh plan can.
    redo_at: UtcDatetime | None = None
    # Informational only, tradeable plans only (parallels atr/stop_atr_multiple):
    # options-implied +/-% move by the nearest usable expiration, and the
    # median actual +/-% move this stock has made around its last reported
    # earnings dates. Neither is a prediction of direction.
    expected_move_pct: float | None = None
    historical_earnings_move_pct: float | None = None
    signal_reasons: str | None = None
    ai_opinion_stance: str | None = None
    ai_opinion_score: int | None = None
    ai_opinion_text: str | None = None
    ai_news_assessment: str | None = None
    # Number of the strategy version (rules + decision-relevant settings) this
    # plan was made under; None for plans from before versioning existed.
    strategy_version: int | None = None
