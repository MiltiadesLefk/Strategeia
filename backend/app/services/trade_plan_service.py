from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime

from sqlmodel import Session, select

from app.analysis.fundamental_scoring import (
    FUNDAMENTAL_SCORE_CAP,
    NEWS_SCORE_CAP,
    score_fundamentals,
    score_news_sentiment,
)
from app.analysis.earnings_history_scoring import (
    SURPRISE_TRACK_RECORD_CAP,
    historical_earnings_move_pct,
    score_earnings_surprise_track_record,
)
from app.analysis.ai_opinion import PARSE_FAILED, ai_opinion_json_schema, parse_ai_opinion_reply
from app.analysis.ai_overlay_scoring import overlay_opposes_trade, score_ai_overlay
from app.analysis.ground_truth import GroundTruthSnapshot, build_ground_truth, find_ungrounded_figures
from app.analysis.expected_move import compute_expected_move_pct, days_to_expiration, score_expected_move
from app.analysis.indicators import latest_atr
from app.analysis.insider_scoring import INSIDER_SCORE_CAP, score_insider_activity
from app.analysis.insight_text import trade_plan_take_text
from app.analysis.macro_calendar import score_macro_event_proximity
from app.analysis.market_confirmation import (
    MARKET_CONFIRMATION_SCORE_CAP,
    MARKET_PROXY_SYMBOL,
    VIX_PROXY_SYMBOL,
    VIX_REGIME_SCORE_CAP,
    score_market_confirmation,
    score_vix_regime,
)
from app.analysis.options_scoring import OPTIONS_SCORE_CAP, score_options_positioning
from app.analysis.scanner_scoring import score_symbol
from app.analysis.shadow_signals import ShadowContext, evaluate_shadow_signals, shadow_signals_from_json, shadow_signals_to_json
from app.analysis.trend import ChartAnalysis, analyze_chart
from app.config import AppSettings, load_app_settings
from app.data_providers.base import AllProvidersFailedError, CompanyOverview, DataProvider, FinancialYear, NewsItem, OptionsSummary
from app.llm_providers.base import LLMProvider
from app.llm_providers.base import DECISION_TIER
from app.llm_providers.factory import generate_with_fallback, generate_with_tier
from app.llm_providers.prompts import AI_OPINION_DATA_MARKER, build_ai_opinion_prompt, build_trade_plan_take_prompt
from app.knowledge.point_in_time import is_simulated
from app.markets import format_market_time, us_closure_reason
from app.portfolio.engine import (
    Clock,
    DuplicatePositionError,
    InsufficientCashError,
    MarketClosedError,
    MaxPositionsExceededError,
    PaperTradingEngine,
    SectorConcentrationError,
    SleeveDisabledError,
    StalePlanError,
)
from app.ml.service import ml_opinion_for_plan, ml_stop_reason, record_ml_opinion
from app.portfolio.models import Sleeve, TradePlanRecord
from app.portfolio.sleeves import CORE_SLEEVE_KEY, plan_sleeve_id, read_scope, scope_clause, scope_of
from app.risk.position_sizing import calculate_position_size, derive_targets
from app.schemas.trade_plan_schemas import TradePlanResponse
from app.services.archive_service import archive_fetched_data
from app.services.deferred_evaluation_service import queue_market_open_redo
from app.services.lesson_notes import past_lessons_block
from app.services.telegram_service import notify_trade_plan
from app.strategy.service import current_strategy_version

logger = logging.getLogger(__name__)

STOP_BUFFER_PCT = 0.01
FALLBACK_STOP_PCT = 0.03

# Volatility floor on the stop. A support level 1% under price is a perfectly
# good stop on a name that moves 0.8% a day and pure noise on one that moves
# 5% — the level-derived stop says nothing about whether it sits inside the
# instrument's normal daily range. Below this multiple of ATR the stop is
# pushed out to it, so "stopped out" means the setup actually broke rather
# than that the stock breathed. 1.5x is the conventional floor for a swing
# stop and keeps the same named-constant, non-ML shape as the rest of
# analysis/.
ATR_PERIOD = 14
ATR_STOP_MULTIPLE = 1.5
TECHNICAL_SCORE_CAP = 6
# Deliberately excludes VIX_REGIME_SCORE_CAP, EXPECTED_MOVE_SCORE_CAP,
# MACRO_EVENT_SCORE_CAP and AI_OVERLAY_SCORE_CAP: all four are
# one-directional (only ever 0 or a penalty, never a bonus — see each
# docstring), so none can contribute to the achievable maximum. Including
# any of them here would inflate the denominator and deflate every
# confidence score even when the corresponding risk is calm/absent and
# contributing nothing. SURPRISE_TRACK_RECORD_CAP is
# included: like fundamental_score/insider_score it's a genuine +/- swing, so
# it belongs in the achievable max the same way they do.
MAX_SCORE_FOR_CONFIDENCE = (
    TECHNICAL_SCORE_CAP
    + FUNDAMENTAL_SCORE_CAP
    + NEWS_SCORE_CAP
    + MARKET_CONFIRMATION_SCORE_CAP
    + OPTIONS_SCORE_CAP
    + INSIDER_SCORE_CAP
    + SURPRISE_TRACK_RECORD_CAP
)
# A straight 0-100 percentage of the achievable evidence: a plan showing 56%
# earned 9 of the 16 points this engine can award, and nothing else is being
# done to the number. It used to be squeezed onto a 20-90 range, which made
# the floor unreachable in both directions and every score harder to reason
# about — a "20%" plan was not weakly-evidenced, it was the worst possible,
# and no plan could ever read below it however bad the evidence was. The
# label stays "Confidence", never "Win Probability" (see notes/Decisions.md):
# it measures how much of the evidence lined up, not the odds of the trade
# working.
CONFIDENCE_FLOOR = 0
CONFIDENCE_CEILING = 100

# "We don't target more trades, but better trades": a symbol clearing the
# trend check still doesn't get a plan unless the combined technical +
# fundamental + news confidence clears the configured bar
# (AppSettings.min_confidence_for_trade). Below it (or on a Neutral trend),
# generate_trade_plan returns/persists an explicit no-trade decision instead
# of forcing a mediocre plan out the door, and deliberately skips the plan
# LLM call in that path too — a rejected symbol doesn't need (and shouldn't
# spend tokens on) an AI narrative, only a rule-based reason.


def _derive_entry_and_stop(
    direction: str,
    price: float,
    support: list[float],
    resistance: list[float],
    atr_value: float | None = None,
) -> tuple[float, float]:
    """Structure first, volatility as a floor: the stop goes just past the
    nearest level, then gets pushed out if that would put it closer than
    ATR_STOP_MULTIPLE x ATR. Never pulled IN — a level further away than the
    ATR floor is respected as-is, since structure is the better reason. A
    missing ATR read (too few bars) just skips the floor."""
    entry = price
    if direction == "long":
        stop = support[0] * (1 - STOP_BUFFER_PCT) if support else entry * (1 - FALLBACK_STOP_PCT)
        if atr_value:
            stop = min(stop, entry - ATR_STOP_MULTIPLE * atr_value)
    else:
        stop = resistance[0] * (1 + STOP_BUFFER_PCT) if resistance else entry * (1 + FALLBACK_STOP_PCT)
        if atr_value:
            stop = max(stop, entry + ATR_STOP_MULTIPLE * atr_value)
    return entry, stop


def clamp_points(total: int) -> int:
    """The raw evidence points behind a confidence percentage, clamped to
    the range the percentage can express. Surfaced on the response because
    confidence is quantised, not continuous: with MAX_SCORE_FOR_CONFIDENCE
    at 16 there are only 17 reachable values, 6.25 points apart, so a bare
    "44%" implies a precision the engine does not have. Showing "7 / 16"
    beside it is the honest version, and matches this codebase's habit of
    displaying the number a verdict came from rather than just the verdict.
    """
    return max(0, min(MAX_SCORE_FOR_CONFIDENCE, total))


def _confidence_score(score: int) -> int:
    clamped = clamp_points(score)
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


def _fetch_confirmation_charts(symbol: str, data_provider: DataProvider) -> tuple[ChartAnalysis | None, ChartAnalysis | None]:
    """Weekly-timeframe and broad-market (SPY) charts for
    market_confirmation.score_market_confirmation — both best-effort, same
    graceful-degradation pattern as fundamentals/news: a fetch failure just
    means that confirmation check contributes 0, never blocks generation.
    2y of weekly bars (not 1y) so EMA50 gets enough runway to be meaningful,
    same reasoning as the 1y-of-daily-bars fix elsewhere in this file."""
    try:
        weekly_ohlcv = data_provider.get_ohlcv(symbol, period="2y", interval="1wk")
        weekly_chart = analyze_chart(weekly_ohlcv)
    except AllProvidersFailedError:
        weekly_chart = None

    market_chart = None
    if symbol != MARKET_PROXY_SYMBOL:
        try:
            market_ohlcv = data_provider.get_ohlcv(MARKET_PROXY_SYMBOL, period="1y", interval="1d")
            market_chart = analyze_chart(market_ohlcv)
        except AllProvidersFailedError:
            market_chart = None

    return weekly_chart, market_chart


def _fetch_vix_level(symbol: str, data_provider: DataProvider) -> float | None:
    """Best-effort latest VIX close for market_confirmation.score_vix_regime
    — a fetch failure (or evaluating the proxy symbol itself) just means
    that check contributes 0, same graceful-degradation pattern throughout
    this module."""
    if symbol == VIX_PROXY_SYMBOL:
        return None
    try:
        vix_ohlcv = data_provider.get_ohlcv(VIX_PROXY_SYMBOL, period="1mo", interval="1d")
        return float(vix_ohlcv["close"].iloc[-1])
    except AllProvidersFailedError:
        return None


@dataclass
class AiOpinion:
    """The overlay's parsed response. A dataclass rather than the tuple this
    used to be: `trade_verdict` made it a five-field return, and the two
    score-shaped fields (`score`, the model's own 0-100 conviction) sat one
    position apart from each other with nothing but argument order keeping
    them straight.

    `stance` is the model's directional read of the stock; `trade_verdict`
    is its answer to "would you take this trade?". They are genuinely
    different questions — see analysis/ai_overlay_scoring.overlay_opposes_trade.
    """

    stance: str | None = None
    trade_verdict: str | None = None
    score: int | None = None
    text: str | None = None
    news_assessment: str | None = None
    # The model that gave this opinion (the decision tier's model), so a plan
    # can say who stopped or waved through the trade. None when no call was made.
    decision_model: str | None = None
    # How the reply was read: "structured" | "lenient" | "failed" (None when no
    # call was made). A model that keeps answering in prose is a guardrail
    # silently scoring 0, so the plan records which path produced the opinion.
    parse_path: str | None = None
    # Specific figures the model quoted that were not in the data it was given
    # (analysis/ground_truth.find_ungrounded_figures). Display and log only:
    # nothing reads this when deciding anything about the trade.
    grounding_warnings: list[str] | None = None


def _parse_ai_opinion(raw_text: str) -> AiOpinion:
    """Read the AI overlay's reply: strict schema validation first, then a
    lenient extraction of a JSON object from damaged output, then a graceful
    give-up that keeps the raw text and claims nothing (see
    analysis/ai_opinion.py). Never raises. An unrecognised or absent verdict
    stays None rather than defaulting to "take": a model that ignored the field
    has not said the trade is fine, and overlay_opposes_trade falls back to the
    stance for it."""
    parsed = parse_ai_opinion_reply(raw_text)
    return AiOpinion(
        parsed.stance, parsed.trade_verdict, parsed.score, parsed.text, parsed.news_assessment,
        parse_path=parsed.parse_path,
    )


def _maybe_get_ai_opinion(
    settings: AppSettings,
    llm_provider: LLMProvider,
    symbol: str,
    chart: ChartAnalysis,
    volume_ratio: float,
    overview: CompanyOverview | None,
    financial_years: list[FinancialYear],
    news: list[NewsItem],
    earnings_date: date | None,
    rule_based_direction: str | None,
    confidence_score: int,
    rule_based_news_reasons: list[str],
    past_lessons: str = "",
    ground_truth: GroundTruthSnapshot | None = None,
) -> AiOpinion:
    """The AI Trading Overlay (Settings, off by default): an independent
    second read of the SAME raw data, alongside — never blended into — the
    rule-based decision above. Only actually calls an LLM when the toggle is
    on AND a real provider is configured; otherwise returns an empty
    AiOpinion so the fields simply stay empty rather than showing a fake or
    rule-based-disguised-as-AI opinion. Also returns a dedicated
    `news_assessment` — the AI actually reads headline substance instead of
    the rule-based engine's plain keyword matching (see
    fundamental_scoring.score_news_sentiment), and is explicitly shown that
    keyword read to contrast against."""
    if not settings.ai_trading_overlay_enabled or llm_provider.name == "none" or not llm_provider.is_configured():
        return AiOpinion()
    if ground_truth is None:
        ground_truth = build_ground_truth(
            symbol, chart, volume_ratio=volume_ratio, earnings_date=earnings_date,
            rule_based_direction=rule_based_direction, rule_based_confidence_pct=confidence_score,
        )
    prompt = build_ai_opinion_prompt(
        symbol, chart, volume_ratio, overview, financial_years, news, earnings_date, rule_based_direction,
        confidence_score, rule_based_news_reasons, past_lessons=past_lessons, ground_truth=ground_truth,
    )
    # The decision tier: this verdict can stop a trade, so it gets the stronger
    # model when one is set. Which model answers is the only thing the tier
    # changes; the overlay is still only ever able to stop a trade. The reply
    # is asked for in the provider's own structured-output mode where it has one
    # (response_schema), and read back with parse_ai_opinion_reply either way.
    result = generate_with_tier(
        llm_provider, prompt, DECISION_TIER, response_schema=ai_opinion_json_schema(),
    )
    if result.error or not result.text:
        logger.warning("AI opinion call failed for %s: %s", symbol, result.error)
        return AiOpinion()
    opinion = replace(_parse_ai_opinion(result.text), decision_model=result.model)
    if opinion.parse_path == PARSE_FAILED:
        # Loud on purpose: with the overlay on, an unreadable reply means the
        # guardrail could not judge this trade at all, and it would otherwise
        # look exactly like "the AI had no objection".
        logger.warning(
            "AI overlay answered for %s but its reply could not be read (no usable stance or verdict); "
            "no objection was recorded. Provider %s, model %s.",
            symbol, llm_provider.name, result.model,
        )
    # An honesty signal only: figures the model quoted that were not in the data
    # it was shown. Never read by the verdict, the score or the sizing.
    data_text = prompt.split(AI_OPINION_DATA_MARKER, 1)[-1]
    warnings = find_ungrounded_figures(f"{opinion.text or ''} {opinion.news_assessment or ''}", ground_truth, data_text)
    if warnings:
        logger.warning("AI overlay for %s quoted figures not in its data: %s", symbol, "; ".join(warnings))
        opinion = replace(opinion, grounding_warnings=warnings)
    return opinion


def _defer_to_market_open(
    session: Session,
    record: TradePlanRecord,
    now: datetime,
    *,
    source: str,
    account_size: float,
    risk_pct: float,
) -> tuple[str, datetime]:
    """D10 = C: the plan was made while its market is closed, so it is left
    pending, never filled, and a fresh evaluation is queued for shortly after
    the next open (see services/deferred_evaluation_service.py). Returns the
    plain-English note for the plan and when the redo is due."""
    reason = us_closure_reason(now)
    deferral = queue_market_open_redo(
        session,
        record.symbol,
        source=source,
        reason=reason,
        now=now,
        trade_plan_id=record.id,
        account_size=account_size,
        risk_pct=risk_pct,
        sleeve_id=record.sleeve_id,
    )
    note = (
        f"Market closed ({reason}), so this plan was not executed. It will be redone from fresh data "
        f"at the next open, {format_market_time(deferral.due_at)}, and only the fresh plan can execute."
    )
    logger.info("Plan for %s deferred to the market open (%s): redo due %s", record.symbol, reason, deferral.due_at)
    return note, deferral.due_at


def _overlay_contradicts_direction(direction: str | None, opinion: AiOpinion) -> bool:
    """Thin wrapper over analysis.ai_overlay_scoring.overlay_opposes_trade,
    kept here because this is where the two settings that read it live.

    Opposition means the model's `trade_verdict` is "pass", or — when it
    gave no usable verdict — its stance is the flat opposite of the
    direction. A Neutral-trend symbol has no direction to object to and
    never counts.

    One setting reads this, at two different strengths —
    `ai_overlay_objection_action` picks which one acts: "cancel" (the
    evaluation becomes an explicit no_trade) or "hold" (the plan is written
    but left pending for a human). Neither one lets the overlay pick `direction`,
    entry, stop or size: those come from the rule-based trend and the risk
    engine, and an objection can only stop a trade, never redirect or
    originate one. The overlay's one graded influence on a NUMBER is
    score_ai_overlay's penalty into confidence_score, which is likewise
    one-directional (see analysis/ai_overlay_scoring.py).
    """
    return overlay_opposes_trade(direction, opinion.stance, opinion.trade_verdict)


def _overlay_ground_truth(
    settings: AppSettings,
    symbol: str,
    chart: ChartAnalysis,
    quote,
    volume_ratio: float,
    atr: float | None,
    overview: CompanyOverview | None,
    earnings_date: date | None,
    options_summary: OptionsSummary | None,
    direction: str | None,
    rule_based_confidence: int,
    rule_based_score: int,
    score_breakdown: tuple[tuple[str, int], ...],
) -> GroundTruthSnapshot | None:
    """The fixed fact block for the overlay prompt, from figures this
    evaluation already holds (analysis/ground_truth.py). Built only when the
    overlay is on, since nothing else reads it."""
    if not settings.ai_trading_overlay_enabled:
        return None
    return build_ground_truth(
        symbol, chart,
        quote_price=quote.price, change_pct=quote.change_pct_24h, volume_ratio=volume_ratio, atr=atr,
        week52_low=overview.week52_low if overview else None,
        week52_high=overview.week52_high if overview else None,
        earnings_date=earnings_date, options_summary=options_summary,
        rule_based_direction=direction, rule_based_confidence_pct=rule_based_confidence,
        rule_based_points=clamp_points(rule_based_score), rule_based_points_max=MAX_SCORE_FOR_CONFIDENCE,
        score_breakdown=score_breakdown,
    )


def generate_trade_plan(
    symbol: str,
    account_size: float,
    risk_pct: float,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    session: Session,
    *,
    allow_auto_execute: bool = True,
    source: str = "manual",
    clock: Clock | None = None,
    settings: AppSettings | None = None,
    sleeve: Sleeve | None = None,
) -> TradePlanResponse:
    """Fully evaluates `symbol` (price/volume/technicals + fundamentals +
    news + earnings) and either returns a tradeable plan or an explicit
    no-trade decision (`direction is None`, `reason` set) — never silently
    skips a symbol. `allow_auto_execute=False` lets a caller (the unattended
    auto-scan loop) request a full evaluation/plan without letting it open a
    paper position, e.g. once the position-count cap is already reached.

    A tradeable plan made while its market is closed is never executed: it
    stays pending and a redo is queued for the next open (D10 = C, see
    _defer_to_market_open). `source` names the caller on that queued redo;
    `clock` is the paper engine's (tests and, later, the backtester pass a
    simulated one). `settings` is the strategy settings to decide with (default:
    the saved ones); a backtest passes its own copy so a run never reads or
    changes the real settings file. `sleeve` is the paper account the plan is
    for (default: the core sleeve): it is recorded on the plan, sizing reads that
    sleeve's cash, and executing the plan opens the position in it. The analysis
    and the decision are identical whichever sleeve asks."""
    settings = settings if settings is not None else load_app_settings()
    in_core = sleeve is None or sleeve.key == CORE_SLEEVE_KEY
    sleeve_id = plan_sleeve_id(session, sleeve)
    sleeve_scope = read_scope(session, None) if in_core else scope_of(sleeve)

    # 1y, matching analysis_service.get_analysis's fetch exactly — trend/EMA/
    # RSI/support-resistance must come from the same lookback window
    # everywhere a symbol is evaluated, or the Analysis page and a trade plan
    # generated for the same symbol at the same moment could read the chart
    # differently. Previously 6mo here; see notes/Decisions.md.
    ohlcv = data_provider.get_ohlcv(symbol, period="1y", interval="1d")
    quote = data_provider.get_quote(symbol)
    chart = analyze_chart(ohlcv)

    volume_ratio = quote.volume / quote.avg_volume_20d if quote.avg_volume_20d > 0 else 1.0
    scan_result = score_symbol(symbol, quote.price, quote.change_pct_24h, chart, volume_ratio)

    # "Smart" layer: fundamentals + news sentiment, on top of the pure
    # technical scanner score — see analysis/fundamental_scoring.py. Fetched
    # (and factored into the trade/no-trade decision) regardless of trend —
    # every symbol (including crypto pairs with no fundamentals/news
    # coverage) gets the full evaluation; those signals just contribute 0
    # when unavailable.
    # Direction is settled before anything is scored against it: fundamentals,
    # news and options positioning are all CONFLUENCE checks, and confluence is
    # meaningless without knowing which way the trade goes. (Previously this
    # was computed further down, so the two fundamental scorers ran blind and
    # charged short setups for their own confirming evidence.)
    provisional_direction = "long" if chart.trend == "Bullish" else "short" if chart.trend == "Bearish" else None

    overview, financial_years, news = _fetch_fundamentals_and_news(symbol, data_provider)
    # Save exactly what was just fetched, dated, for future backtests. After
    # the fetch and outside every scorer on purpose: it never changes a number
    # here, and archive_fetched_data swallows its own failures so a database
    # hiccup can't fail (or alter) a trade decision.
    archive_fetched_data(
        session, symbol, overview=overview, financial_years=financial_years, news=news, data_provider=data_provider
    )
    earnings_date = data_provider.get_earnings_date(symbol)
    news_score, news_reasons = score_news_sentiment(news, provisional_direction)
    fundamental_score, fundamental_reasons = (
        score_fundamentals(overview, financial_years, earnings_date, quote.price, provisional_direction)
        if overview
        else (0, [])
    )
    # Confluence: does the weekly timeframe / broad market (SPY) agree with
    # the daily-chart direction? See analysis/market_confirmation.py — kept
    # as its own capped dimension, same pattern as fundamental_score/
    # news_score, never folded into scanner_scoring's own 0-6 technical
    # score (that would change the Market Scanner's score/signal tiers too).
    weekly_chart, market_chart = _fetch_confirmation_charts(symbol, data_provider)
    market_confirmation_score, market_confirmation_reasons = score_market_confirmation(
        provisional_direction, weekly_chart, market_chart
    )

    # VIX regime (one-directional risk flag, not confluence — see
    # score_vix_regime) and options positioning (put/call volume skew —
    # analysis/options_scoring.py). Both best-effort: no chain/VIX data
    # available just means that check contributes 0.
    vix_level = _fetch_vix_level(symbol, data_provider)
    vix_regime_score, vix_regime_reasons = score_vix_regime(vix_level)
    options_summary: OptionsSummary | None = data_provider.get_options_summary(symbol)
    options_score, options_reasons = score_options_positioning(provisional_direction, options_summary)

    # Open-market insider buying (SEC Form 4). Best-effort like every other
    # confluence check: a non-registrant (crypto) or a failed fetch contributes
    # 0 rather than blocking the evaluation. See analysis/insider_scoring.py
    # for why selling is deliberately never scored.
    insider_activity = data_provider.get_insider_activity(symbol)
    insider_score, insider_reasons = score_insider_activity(provisional_direction, insider_activity)

    # Forward-looking, but on the market's OWN pricing/record, never a guess
    # at unpublished content — see each module's docstring for why this is a
    # legitimate "picture before it comes out" rather than fabrication.
    atr14 = latest_atr(ohlcv, ATR_PERIOD)
    atr_pct = (atr14 / chart.price * 100) if atr14 and chart.price else None
    expected_move_score, expected_move_reasons = score_expected_move(
        provisional_direction, options_summary, atr_pct
    )
    earnings_history = data_provider.get_earnings_history(symbol)
    earnings_surprise_score, earnings_surprise_reasons = score_earnings_surprise_track_record(
        provisional_direction, earnings_history
    )
    # backtest: this reads today's real date, so inside a simulated moment it
    # would apply the CURRENT week's macro releases to every past day. The
    # price-only backtest leaves macro events out entirely (0 points).
    macro_event_score, macro_event_reasons = (0, []) if is_simulated() else score_macro_event_proximity()

    rule_based_score = (
        scan_result.score + fundamental_score + news_score + market_confirmation_score
        + vix_regime_score + options_score + insider_score
        + expected_move_score + earnings_surprise_score + macro_event_score
    )
    # The purely rule-based read, computed before the overlay is consulted —
    # this is the number handed to the overlay prompt as context (see
    # build_ai_opinion_prompt: "for context only, never as an answer key"),
    # so the model is shown what the deterministic engine concluded on its
    # own rather than a figure its own opinion already moved. Feeding it the
    # post-overlay score would make the prompt's context partly an echo of
    # the model's own previous reasoning.
    rule_based_confidence = _confidence_score(rule_based_score)
    technical_reason = f"{chart.trend} trend with {chart.momentum.lower()} momentum"

    # AI Trading Overlay (opt-in, Settings): an independent second opinion
    # from the SAME raw data, computed once here so both the no-trade and
    # tradeable paths below can attach it. Its influence is bounded and
    # one-directional in every form — see the ai_overlay_* settings block in
    # config.py and analysis/ai_overlay_scoring.py.
    opinion = _maybe_get_ai_opinion(
        settings, llm_provider, symbol, chart, volume_ratio, overview, financial_years, news, earnings_date,
        provisional_direction, rule_based_confidence, news_reasons,
        # lesson notes: earlier AI write-ups of closed trades in this symbol, shown to the
        # overlay as notes only. Like everything the overlay sees, they can only inform an
        # opinion that may stop this trade; they cannot start or reshape one.
        past_lessons=past_lessons_block(session, symbol) if settings.ai_trading_overlay_enabled else "",
        # ground truth: the fixed fact block the overlay's prompt carries (computed here, without AI).
        ground_truth=_overlay_ground_truth(
            settings, symbol, chart, quote, volume_ratio, atr14, overview, earnings_date, options_summary,
            provisional_direction, rule_based_confidence, rule_based_score,
            (
                ("technical", scan_result.score), ("fundamentals", fundamental_score), ("news", news_score),
                ("market confirmation", market_confirmation_score), ("VIX regime", vix_regime_score),
                ("options", options_score), ("insider", insider_score), ("expected move", expected_move_score),
                ("earnings track record", earnings_surprise_score), ("macro events", macro_event_score),
            ),
        ),
    )
    ai_opinion_stance = opinion.stance
    ai_opinion_score = opinion.score
    ai_opinion_text = opinion.text
    ai_news_assessment = opinion.news_assessment
    ai_trade_verdict = opinion.trade_verdict
    ai_decision_model = opinion.decision_model
    ai_opinion_parse = opinion.parse_path
    ai_grounding_warnings = "\n".join(opinion.grounding_warnings) if opinion.grounding_warnings else None

    # Rung 1 of the ladder: a disagreeing overlay costs conviction, scored
    # like any other dimension. Never a bonus, so it can only ever talk the
    # engine out of a trade, never into one.
    ai_overlay_score, ai_overlay_reasons = (
        score_ai_overlay(provisional_direction, opinion.stance, opinion.trade_verdict, opinion.score)
        if settings.ai_overlay_scores_confidence
        else (0, [])
    )
    combined_score = rule_based_score + ai_overlay_score
    confidence_score = _confidence_score(combined_score)
    signal_reasons = "; ".join(
        [technical_reason, *fundamental_reasons, *news_reasons, *market_confirmation_reasons,
         *vix_regime_reasons, *options_reasons, *insider_reasons,
         *expected_move_reasons, *earnings_surprise_reasons, *macro_event_reasons,
         *ai_overlay_reasons]
    )

    # "cancel": the objection ends the evaluation as an explicit no-trade
    # decision rather than writing a plan the engine's own second opinion
    # expects to lose. Distinct from the confidence penalty in kind, not
    # just degree — that is capped at 3 points and a high-conviction setup
    # absorbs it, whereas this stops the trade outright.
    overlay_vetoes_trade = settings.ai_overlay_objection_action == "cancel" and _overlay_contradicts_direction(
        provisional_direction, opinion
    )

    # Which rules made this decision, recorded on the plan (and on a no-trade
    # record: a rejection is a decision too). Looked up here, after every data
    # fetch has succeeded, so a failed evaluation leaves no version behind.
    strategy_version = current_strategy_version(session, settings).number

    # Silent signals: recorded on the plan, never scored (analysis/shadow_signals.py).
    # Evaluated after every decision input above and consumed by nothing that
    # decides: not the score, the direction, the sizing or the overlay.
    shadow_signals_json = shadow_signals_to_json(
        evaluate_shadow_signals(ShadowContext(symbol=symbol, direction=provisional_direction, session=session))
    )

    # ML style (Forecast Lab): only for an "ml" sleeve, only for a trade the rules
    # already approve, and it can only STOP it. Direction, entry, stop and size are
    # never read from it. None when the style is not in play.
    ml_opinion = None
    if chart.trend != "Neutral" and not overlay_vetoes_trade and confidence_score >= settings.min_confidence_for_trade:
        ml_opinion = ml_opinion_for_plan(
            session,
            settings,
            sleeve,
            scores={
                "technical_score": scan_result.score,
                "fundamental_score": fundamental_score,
                "news_score": news_score,
                "market_confirmation_score": market_confirmation_score,
                "vix_regime_score": vix_regime_score,
                "options_score": options_score,
                "insider_score": insider_score,
                "expected_move_score": expected_move_score,
                "earnings_surprise_score": earnings_surprise_score,
                "macro_event_score": macro_event_score,
            },
            confidence_points=clamp_points(combined_score),
            direction=provisional_direction,
        )
    ml_stops_trade = ml_opinion is not None and ml_opinion.stops_trade

    if chart.trend == "Neutral" or overlay_vetoes_trade or ml_stops_trade or confidence_score < settings.min_confidence_for_trade:
        if chart.trend == "Neutral":
            reason = "No clear trend (EMA20/EMA50 not aligned) — not enough information to size a trade."
        elif overlay_vetoes_trade:
            # Named ahead of the confidence bar deliberately: when the
            # overlay's own penalty is what pushed the score under
            # min_confidence_for_trade, "confidence too low" would report
            # the symptom and hide the cause.
            conviction = f" at {ai_opinion_score}% conviction" if ai_opinion_score is not None else ""
            if ai_trade_verdict == "pass":
                reason = (
                    f"AI Trading Overlay would not take this trade{conviction} — "
                    f"no trade taken against a rule-based {provisional_direction}."
                )
            else:
                reason = (
                    f"AI Trading Overlay reads this {ai_opinion_stance}{conviction} against a rule-based "
                    f"{provisional_direction} — no trade taken on a flat disagreement."
                )
            logger.info(
                "Trade vetoed for %s: overlay opposes rule-based %s (verdict=%s, stance=%s)",
                symbol, provisional_direction, ai_trade_verdict, ai_opinion_stance,
            )
        elif ml_stops_trade:
            reason = ml_stop_reason(ml_opinion, provisional_direction)
        else:
            reason = (
                f"Confidence too low ({confidence_score}%, needs {settings.min_confidence_for_trade}%+) "
                f"despite a {chart.trend.lower()} trend."
            )
        record = TradePlanRecord(
            symbol=symbol,
            status="no_trade",
            reason=reason,
            confidence_score=confidence_score,
            technical_score=scan_result.score,
            fundamental_score=fundamental_score,
            news_score=news_score,
            market_confirmation_score=market_confirmation_score,
            vix_regime_score=vix_regime_score,
            options_score=options_score,
            insider_score=insider_score,
            expected_move_score=expected_move_score,
            earnings_surprise_score=earnings_surprise_score,
            macro_event_score=macro_event_score,
            ai_overlay_score=ai_overlay_score,
            ai_trade_verdict=ai_trade_verdict,
            ai_decision_model=ai_decision_model,
            ai_opinion_parse=ai_opinion_parse,
            ai_grounding_warnings=ai_grounding_warnings,
            confidence_points=clamp_points(combined_score),
            confidence_points_max=MAX_SCORE_FOR_CONFIDENCE,
            signal_reasons=signal_reasons,
            ai_opinion_stance=ai_opinion_stance,
            ai_opinion_score=ai_opinion_score,
            ai_opinion_text=ai_opinion_text,
            ai_news_assessment=ai_news_assessment,
            strategy_version=strategy_version,
            shadow_signals=shadow_signals_json,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        record_ml_opinion(session, record.id, ml_opinion)
        return TradePlanResponse(
            id=record.id,
            symbol=symbol,
            direction=None,
            reason=reason,
            confidence_score=confidence_score,
            status=record.status,
            created_at=record.created_at,
            technical_score=scan_result.score,
            fundamental_score=fundamental_score,
            news_score=news_score,
            market_confirmation_score=market_confirmation_score,
            vix_regime_score=vix_regime_score,
            options_score=options_score,
            insider_score=insider_score,
            expected_move_score=expected_move_score,
            earnings_surprise_score=earnings_surprise_score,
            macro_event_score=macro_event_score,
            ai_overlay_score=ai_overlay_score,
            ai_trade_verdict=ai_trade_verdict,
            ai_decision_model=ai_decision_model,
            ai_opinion_parse=ai_opinion_parse,
            ai_grounding_warnings=ai_grounding_warnings,
            confidence_points=clamp_points(combined_score),
            confidence_points_max=MAX_SCORE_FOR_CONFIDENCE,
            ai_opinion_stance=ai_opinion_stance,
            ai_opinion_score=ai_opinion_score,
            ai_opinion_text=ai_opinion_text,
            ai_news_assessment=ai_news_assessment,
            signal_reasons=signal_reasons,
            strategy_version=strategy_version,
            shadow_signals=shadow_signals_from_json(shadow_signals_json),
        )

    direction = "long" if chart.trend == "Bullish" else "short"
    atr_value = atr14  # already computed above, for score_expected_move
    entry, stop = _derive_entry_and_stop(direction, chart.price, chart.support, chart.resistance, atr_value)

    # Sized against the cash the account can ACTUALLY deploy, not just the
    # nominal account size. Without available_cash, `capped_by_cash` could
    # never be True, so the plan advertised a share count the engine then
    # silently shrank at open — the UI showed a position the account could
    # not take.
    engine = PaperTradingEngine(
        session,
        data_provider,
        account_size if in_core else sleeve.starting_cash,
        settings.max_concurrent_positions,
        slippage_bps=settings.slippage_bps,
        commission_per_trade=settings.commission_per_trade,
        max_positions_per_sector=settings.max_positions_per_sector,
        max_position_pct_of_adv=settings.max_position_pct_of_adv,
        clock=clock,
        max_holding_days=settings.max_holding_days,
        liquidity_slippage_coefficient=settings.liquidity_slippage_coefficient if settings.liquidity_slippage_enabled else None,
        scale_out_fraction=settings.scale_out_fraction if settings.scale_out_enabled else None,
        scale_out_stop_mode=settings.scale_out_stop_mode,
        scale_out_trail_r=settings.scale_out_trail_r,
        sleeve=None if in_core else sleeve,
    )
    sizing = calculate_position_size(account_size, risk_pct, entry, stop, engine.available_cash())
    targets = derive_targets(entry, stop, direction, chart.support, chart.resistance)
    stop_atr_multiple = (abs(entry - stop) / atr_value) if atr_value else None
    # Shown regardless of whether it moved the score — same "surface the
    # number, not just the verdict" pattern as ATR/MACD. options_summary and
    # earnings_history were already fetched above for scoring; this reuses
    # them rather than fetching again.
    expected_move_days = days_to_expiration(options_summary.expiration) if options_summary else None
    expected_move_pct = (
        compute_expected_move_pct(options_summary.atm_implied_volatility, expected_move_days)
        if options_summary and options_summary.atm_implied_volatility and expected_move_days
        else None
    )
    historical_move_pct = historical_earnings_move_pct(ohlcv, earnings_history)

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
    # Only this sleeve's own pending plans: another sleeve's pending plan for the
    # same symbol is a different account's, and stays.
    stale_query = select(TradePlanRecord).where(TradePlanRecord.symbol == symbol, TradePlanRecord.status == "pending")
    stale_query = stale_query.where(scope_clause(TradePlanRecord.sleeve_id, sleeve_scope))
    stale_pending = session.exec(stale_query).all()
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
        market_confirmation_score=market_confirmation_score,
        vix_regime_score=vix_regime_score,
        options_score=options_score,
        insider_score=insider_score,
        expected_move_score=expected_move_score,
        earnings_surprise_score=earnings_surprise_score,
        macro_event_score=macro_event_score,
        ai_overlay_score=ai_overlay_score,
        ai_trade_verdict=ai_trade_verdict,
        ai_decision_model=ai_decision_model,
        ai_opinion_parse=ai_opinion_parse,
        ai_grounding_warnings=ai_grounding_warnings,
        confidence_points=clamp_points(combined_score),
        confidence_points_max=MAX_SCORE_FOR_CONFIDENCE,
        signal_reasons=signal_reasons,
        ai_opinion_stance=ai_opinion_stance,
        ai_opinion_score=ai_opinion_score,
        ai_opinion_text=ai_opinion_text,
        ai_news_assessment=ai_news_assessment,
        strategy_version=strategy_version,
        shadow_signals=shadow_signals_json,
        sleeve_id=sleeve_id,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    record_ml_opinion(session, record.id, ml_opinion)
    session.refresh(record)

    auto_execute_note = ""
    redo_at: datetime | None = None
    if not engine.is_market_open_for(symbol):
        # D10 = C, and ahead of the auto-execute switch on purpose: with
        # auto-execute off the plan still describes the last session's
        # prices, and the Execute button is refused for it until the fresh
        # plan replaces it. Nothing below (the overlay hold, the fill) is
        # evaluated for a plan that will be redone anyway — the redo runs
        # every one of those checks again on the open's data.
        auto_execute_note, redo_at = _defer_to_market_open(
            session, record, engine.now(), source=source, account_size=account_size, risk_pct=risk_pct
        )
        record.auto_execute_note = auto_execute_note
        session.add(record)
        session.commit()
        session.refresh(record)
        auto_execute_note = f"\n{auto_execute_note}"
    elif settings.auto_execute_trade_plans and allow_auto_execute:
        if settings.ai_overlay_objection_action == "hold" and _overlay_contradicts_direction(direction, opinion):
            # "hold": the plan is written and left pending for a human,
            # on the reasoning that the one moment a second opinion is
            # worth having is the moment before capital commits.
            # Mutually exclusive with "cancel" by construction rather than
            # by accident — same trigger, and a cancelled evaluation
            # returned a no_trade record long before reaching this line.
            # Entry/stop/size are untouched either way; only whether
            # open_position() gets CALLED changes here.
            confidence_note = f" ({ai_opinion_score}% confidence)" if ai_opinion_score is not None else ""
            called_it = (
                f"would not take this trade{confidence_note}"
                if ai_trade_verdict == "pass"
                else f"called this {ai_opinion_stance}{confidence_note}"
            )
            auto_execute_note = (
                f"\nAuto-execute held: AI Trading Overlay {called_it} "
                f"against a rule-based {direction} — left pending for manual review."
            )
            logger.info(
                "Auto-execute held for %s: overlay (%s) contradicts rule-based %s",
                symbol, ai_opinion_stance, direction,
            )
        else:
            try:
                engine.open_position(record)
                session.refresh(record)
                auto_execute_note = "\nAuto-executed as a paper position."
            except MarketClosedError:
                # The bell rang between the session check above and the fill
                # (a plan finishing at 15:59:59). Same outcome as a plan made
                # after the close: pending, and redone at the next open.
                note, redo_at = _defer_to_market_open(
                    session, record, engine.now(), source=source, account_size=account_size, risk_pct=risk_pct
                )
                auto_execute_note = f"\n{note}"
            except (
                InsufficientCashError,
                DuplicatePositionError,
                MaxPositionsExceededError,
                SectorConcentrationError,
                SleeveDisabledError,
                StalePlanError,
            ) as exc:
                logger.warning("Auto-execute skipped for %s: %s", symbol, exc)
                auto_execute_note = f"\nAuto-execute skipped: {exc}"

        # Persisted so the API/UI can show WHY a plan is still "pending"
        # without needing Telegram configured — previously this note was
        # computed and then only ever folded into the Telegram text below,
        # discarded everywhere else. Safe to commit again here: open_position
        # already committed and this function already refreshed `record`
        # right after (see CLAUDE.md's attribute-expiry note) — refreshing
        # again after this commit keeps that guarantee for the caller.
        record.auto_execute_note = auto_execute_note.strip()
        session.add(record)
        session.commit()
        session.refresh(record)

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
        capped_by_cash=sizing.capped_by_cash,
        atr=atr_value,
        stop_atr_multiple=stop_atr_multiple,
        expected_move_pct=expected_move_pct,
        historical_earnings_move_pct=historical_move_pct,
        confidence_score=confidence_score,
        time_horizon=record.time_horizon,
        auto_execute_note=record.auto_execute_note,
        redo_at=redo_at,
        ai_take_text=llm_result.text,
        ai_provider=llm_result.provider,
        status=record.status,
        created_at=record.created_at,
        technical_score=scan_result.score,
        fundamental_score=fundamental_score,
        news_score=news_score,
        market_confirmation_score=market_confirmation_score,
        vix_regime_score=vix_regime_score,
        options_score=options_score,
        insider_score=insider_score,
        expected_move_score=expected_move_score,
        earnings_surprise_score=earnings_surprise_score,
        macro_event_score=macro_event_score,
        ai_overlay_score=ai_overlay_score,
        ai_trade_verdict=ai_trade_verdict,
        ai_decision_model=ai_decision_model,
        ai_opinion_parse=ai_opinion_parse,
        ai_grounding_warnings=ai_grounding_warnings,
        confidence_points=clamp_points(combined_score),
        confidence_points_max=MAX_SCORE_FOR_CONFIDENCE,
        signal_reasons=signal_reasons,
        ai_opinion_stance=ai_opinion_stance,
        ai_opinion_score=ai_opinion_score,
        ai_opinion_text=ai_opinion_text,
        ai_news_assessment=ai_news_assessment,
        strategy_version=strategy_version,
        shadow_signals=shadow_signals_from_json(shadow_signals_json),
    )
