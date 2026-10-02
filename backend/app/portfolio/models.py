from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from sqlmodel import Field, SQLModel

from app.timeutil import utcnow_naive

TradePlanStatus = Literal["pending", "executed", "discarded", "no_trade"]
PositionStatus = Literal["open", "closed"]
CloseReason = Literal["stop_hit", "tp1_hit", "time_exit", "manual"]


class TradePlanRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str
    # Null direction/entry/stop/targets/sizing mean the engine looked at this
    # symbol and explicitly decided NOT to trade it (status="no_trade") —
    # see `reason` — rather than every evaluation forcing a tradeable plan
    # regardless of quality. See notes/Decisions.md.
    direction: Optional[str] = None
    entry: Optional[float] = None
    stop: Optional[float] = None
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    rr1: Optional[float] = None
    rr2: Optional[float] = None
    suggested_shares: Optional[int] = None
    account_risk_dollars: Optional[float] = None
    confidence_score: int
    ai_take_text: Optional[str] = None
    ai_provider: Optional[str] = None
    reason: Optional[str] = None
    # Independent AI opinion (Settings -> AI Trading Overlay, off by
    # default) — a second, separately-labeled read from the SAME raw data,
    # never blended into direction/confidence_score above. Null unless the
    # overlay was on AND a real LLM provider was configured for this
    # evaluation. See notes/Decisions.md.
    ai_opinion_stance: Optional[str] = None
    ai_opinion_score: Optional[int] = None
    ai_opinion_text: Optional[str] = None
    # The AI's own read of headline substance — distinct from `ai_opinion_text`
    # (the overall trade opinion) because it's the one place the app lets the
    # AI genuinely analyze news instead of the rule-based engine's plain
    # keyword matching (fundamental_scoring.score_news_sentiment). See
    # notes/Decisions.md.
    ai_news_assessment: Optional[str] = None
    time_horizon: str = "1-4 weeks"
    status: str = "pending"
    created_at: datetime = Field(default_factory=utcnow_naive)
    # "Smart" signal breakdown, added alongside the technical scanner score —
    # see analysis/fundamental_scoring.py. Nullable so old rows (generated
    # before this field existed) just read back as None, not an error.
    technical_score: Optional[int] = None
    fundamental_score: Optional[int] = None
    news_score: Optional[int] = None
    # Weekly-timeframe + broad-market (SPY) agreement, +/-2 total — see
    # analysis/market_confirmation.py. Deliberately separate from
    # technical_score (scanner_scoring's own 0-6) rather than folded into
    # it, so the Market Scanner's score/signal tiers stay unaffected.
    market_confirmation_score: Optional[int] = None
    # VIX regime flag, -1 or 0 only (never positive — see
    # market_confirmation.score_vix_regime) and options put/call
    # positioning skew, +/-1 (analysis/options_scoring.py). Both minor,
    # independently-capped factors, same "shown, never hidden" pattern.
    vix_regime_score: Optional[int] = None
    options_score: Optional[int] = None
    # Open-market insider buying, +/-1 (analysis/insider_scoring.py). Same
    # "shown, never hidden" pattern; nullable so pre-existing rows read None.
    insider_score: Optional[int] = None
    # Forward-looking dimensions on the market's own current pricing/record,
    # never a guess at unpublished content — see each analysis module's
    # docstring. expected_move/macro_event are one-directional (0 or a
    # penalty only); earnings_surprise is a genuine +/- like fundamental_score.
    expected_move_score: Optional[int] = None
    earnings_surprise_score: Optional[int] = None
    macro_event_score: Optional[int] = None
    # Points the AI Trading Overlay's disagreement cost the confidence math:
    # 0 or negative, never positive (analysis/ai_overlay_scoring.py explains
    # the one-directional asymmetry). NOT the same field as
    # ai_opinion_score above, which is the AI's own stated 0-100 conviction
    # in its own stance — this is what that stance did to confidence_score.
    ai_overlay_score: Optional[int] = None
    # "take" | "pass" | None — the AI's direct answer to "would you take this
    # trade?", which is a different question from ai_opinion_stance ("where
    # does the stock go?") and the one actually acted on. None means the
    # model gave no usable verdict (older rows, or a provider that ignored
    # the field), in which case the stance is used as the fallback signal.
    ai_trade_verdict: Optional[str] = None
    # The model that gave the overlay's verdict above (the decision tier's
    # model, which can differ from the one that wrote ai_take_text). None when
    # the overlay did not run, and on rows from before this was recorded.
    ai_decision_model: Optional[str] = None
    # How the overlay's reply was read: "structured" (passed the schema),
    # "lenient" (a JSON object was pulled out of a damaged reply) or "failed"
    # (nothing usable; no objection could be recorded). Null when the overlay
    # did not answer, and on rows from before this was recorded.
    ai_opinion_parse: Optional[str] = None
    # Figures the overlay's reasoning quoted that were not in the data it was
    # shown, one per line (analysis/ground_truth.py). An honesty signal for the
    # reader: it never feeds the verdict, the score or the sizing. Null = none.
    ai_grounding_warnings: Optional[str] = None
    signal_reasons: Optional[str] = None  # "; "-joined, human-readable — not JSON, kept simple
    # What happened at the auto-execute step, in plain English — executed,
    # skipped (insufficient cash / duplicate / at position cap / sector cap /
    # stale price), or held for manual review because the AI Trading Overlay
    # disagreed. This used to be computed and folded straight into the
    # Telegram message with nothing kept for the API/UI, so anyone without
    # Telegram configured had no way to see WHY a plan stayed pending. Null
    # on a no_trade record (auto-execute is never attempted there) and on any
    # plan generated before this field existed.
    auto_execute_note: Optional[str] = None
    # The strategy version (app/strategy) this plan was made under: the number
    # of the StrategyVersion row whose settings + rule constants were in force
    # when it was generated. Null on plans from before versioning existed.
    strategy_version: Optional[int] = None
    # Silent signals (analysis/shadow_signals.py): JSON list of what each new,
    # not-yet-scored signal read on this plan and the points it WOULD have
    # added. Recorded for later backtesting; never part of confidence_score,
    # the direction, the sizing or any decision. Null on older plans.
    shadow_signals: Optional[str] = None
    # The sleeve (paper account) this plan was made for: the id of a Sleeve row.
    # Null reads as the core sleeve, so plans from before sleeves existed need no
    # backfill. Executing the plan opens its position in this sleeve.
    sleeve_id: Optional[int] = Field(default=None, index=True)


class PaperPosition(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    trade_plan_id: Optional[int] = Field(default=None, foreign_key="tradeplanrecord.id")
    symbol: str
    direction: str
    entry_price: float
    # What the plan asked for, vs entry_price = what it actually filled at
    # (current market + slippage — see PaperTradingEngine._resolve_entry_price).
    # Nullable: rows written before the engine re-quoted at open read back None.
    planned_entry_price: Optional[float] = None
    stop_loss: float
    tp1: float
    tp2: float
    shares: int
    opened_at: datetime = Field(default_factory=utcnow_naive)
    status: str = "open"
    closed_at: Optional[datetime] = None
    close_price: Optional[float] = None
    close_reason: Optional[str] = None
    realized_pnl: Optional[float] = None
    realized_r: Optional[float] = None
    # Round-trip commission, charged at open and again at close. realized_pnl
    # is already net of it; kept separately so the UI can show gross vs net.
    fees_paid: Optional[float] = None
    # Best and worst price during the trade (MFE / MAE, see portfolio/excursion.py),
    # as non-negative magnitudes: how far the price went in the trade's favour /
    # against it, as % of entry and in R (multiples of |entry - stop|). Set when
    # the position closes; None for rows closed before this existed and whenever
    # the bars were not available (never a guess). Open positions get theirs
    # computed on the fly, not stored.
    mfe_pct: Optional[float] = None
    mae_pct: Optional[float] = None
    mfe_r: Optional[float] = None
    mae_r: Optional[float] = None
    # How the exit price was placed in time (see portfolio/intraday.py): "daily"
    # (one level on a daily bar, or the open already settled the order), "hourly"
    # (found on an hourly bar), "daily_ambiguous_stop_first" (a daily bar held both
    # levels and no hourly answer was available, so the stop was taken),
    # "hourly_ambiguous_stop_first" (both levels in one hourly bar). None for a
    # manual close and for rows closed before this was recorded.
    exit_resolution: Optional[str] = None
    # Whether the rest of the entry day (after the position opened) has been checked
    # hour by hour: "hourly" once every hour is covered, "daily_only" when the hourly
    # history could not reach back to that day. None = not checked yet.
    entry_day_check: Optional[str] = None
    # The after-the-fact lesson an AI wrote for this closed trade (see
    # services/lesson_service.py): 2-4 sentences, plain text. Null until one is
    # written, and ALSO null when the AI could not write one: no template stands
    # in for it, because a canned "lesson" would be invented insight. lesson_at is
    # when the last attempt was made (a success, or a failure the retry delay is
    # counted from); lesson_error is why the last attempt failed (short), and is
    # cleared by a later success.
    lesson_text: Optional[str] = None
    lesson_provider: Optional[str] = None
    lesson_model: Optional[str] = None
    lesson_at: Optional[datetime] = None
    lesson_error: Optional[str] = None
    # Which sleeve (paper account) holds this position. Null reads as the core
    # sleeve (positions from before sleeves existed); see portfolio/sleeves.py.
    sleeve_id: Optional[int] = Field(default=None, index=True)


DeferredEvaluationStatus = Literal["pending", "done", "skipped", "failed"]


class DeferredEvaluation(SQLModel, table=True):
    """A symbol whose evaluation has to wait for its market to open.

    Decision D10 = C (plan.md): a tradeable plan made while the market is
    closed is never filled. It stays pending, and a fresh evaluation from
    fresh data is queued here for shortly after the next open; only the plan
    that fresh evaluation produces may execute (services/automation_service
    .run_market_open_redos). Generic on purpose — WA-6's watchers will queue
    their off-hours re-checks in the same table, so nothing here assumes a
    trade plan exists: `source` says who asked, `trade_plan_id` is optional.

    One pending row per symbol at most: a second off-hours request for the
    same symbol updates the pending row instead of queuing a duplicate redo.
    Times are naive UTC like every other column.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    # Who asked for the evaluation that had to wait: "manual" (Generate
    # Trade Plan), "auto_scan", "market_open_redo", later "watcher:<name>".
    source: str
    # Why it waited, in plain words ("weekend", "Thanksgiving Day", ...).
    reason: str
    requested_at: datetime = Field(default_factory=utcnow_naive)
    # The redo runs at or after this moment, and only while the market is
    # open — so a job that missed it (app down at 09:45) still catches up.
    due_at: datetime
    status: str = Field(default="pending", index=True)
    attempts: int = 0
    resolved_at: Optional[datetime] = None
    # The off-hours plan being redone, when there is one. Null for a request
    # that never had a plan (a watcher event).
    trade_plan_id: Optional[int] = Field(default=None, foreign_key="tradeplanrecord.id")
    # The fresh plan (or no_trade record) the redo produced.
    result_plan_id: Optional[int] = Field(default=None, foreign_key="tradeplanrecord.id")
    # What happened, in plain English, once resolved (or why a retry is due).
    outcome: Optional[str] = None
    # The sizing inputs of the original request, so the redo sizes the same
    # way. Null means "the Settings defaults at redo time".
    account_size: Optional[float] = None
    risk_pct: Optional[float] = None
    # The sleeve the redo should trade in (null = core), so a plan made for one
    # sleeve is redone for that sleeve and not for the default one.
    sleeve_id: Optional[int] = None


class EquitySnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=utcnow_naive)
    equity_value: float
    cash_balance: float
    # Which sleeve's curve this point belongs to (null = core, see Sleeve).
    sleeve_id: Optional[int] = Field(default=None, index=True)


class AccountState(SQLModel, table=True):
    """The cash of ONE sleeve: one row per sleeve. sleeve_id null = core."""

    id: Optional[int] = Field(default=None, primary_key=True)
    starting_cash: float
    current_cash: float
    sleeve_id: Optional[int] = Field(default=None, index=True)


class Sleeve(SQLModel, table=True):
    """An isolated paper account for one trading style.

    Each sleeve has its own cash, positions, statistics and equity curve; the
    strategy settings (risk %, caps, slippage ...) stay shared. The built-in
    `core` sleeve ("Swing (rules)") is the account the app always had: it is
    created the first time something needs it and every row that carries no
    sleeve_id (everything written before sleeves existed) belongs to it.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)  # url-safe slug, e.g. "core", "ai-committee"
    name: str
    style: str  # free-text label of the trading style, e.g. "swing", "momentum"
    # The starting cash a NEW account for this sleeve is seeded with. The core
    # sleeve follows Settings -> Paper Account instead (its value here is unused).
    starting_cash: float
    enabled: bool = True  # a disabled sleeve opens nothing new; its open positions are still managed
    color: Optional[str] = None  # chip colour (hex), shown wherever the sleeve is named
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow_naive)
