"""The missed-trades ledger: what the trades we did NOT take would have earned.

The app writes a `no_trade` plan whenever it declines a symbol (confidence under
the bar, no trend, the AI overlay objecting) and leaves some written plans
pending (held back by the overlay, refused by the engine for cash or a cap). It
never learns whether declining was right. Without that, nobody can tell whether
`min_confidence_for_trade` or the AI veto helps or hurts. This module builds the
counterfactual and stores it, so the report (missed_trade_report.py) can compare
the declined trades with the taken ones. The idea (replay what every declined
decision would have earned, as a ledger kept apart from the real results) is from
the Phil project's counterfactual ledger; the code here is written for this
engine's own rules.

Three steps:

1. **Classify** every plan that was not executed (`classify_plan`, one tested
   function matching the exact status / reason / note strings the live code
   writes).
2. **Rebuild the trade as it stood at the decision.** A no-trade record has no
   entry, stop or target, so they are rebuilt with the same functions the live
   path uses (`analyze_chart`, `_derive_entry_and_stop`, `latest_atr`,
   `derive_targets`) on the daily bars that were final when the plan was made, and
   nothing later. A plan that was written (held, refused) keeps its own stored
   numbers.
3. **Walk the bars after the decision** with the paper engine's own exit scan
   (`PaperTradingEngine._scan_exit`: the first stop or TP1 touch, stop before TP1
   inside a bar, gaps modelled, the optional holding limit), and compute R the way
   `close_position` does (slippage and commission included).

Where this is deliberately rougher than the live engine, and why it still counts:

* **Entry price.** The quote the plan used is not stored. The entry is the close
  of the last daily bar that was final when the plan was made. A plan made during
  a session therefore enters at the previous close, and the day it was made on is
  skipped (the live engine skips its own entry bar too).
* **Daily bars only.** The live engine looks at hourly bars to order a stop and a
  target inside one daily bar and to check the rest of the entry day. This does
  not: when one bar touches both levels the stop is taken (the engine's own
  fallback), which can only be the unfavourable reading for the hypothetical trade.
  Every outcome records `resolution = "daily"`.
* **Today's rules.** The levels are rebuilt with the current constants (stop buffer,
  ATR multiple, target rules), not the ones in force when the plan was made.
* **Adjusted prices.** The history store holds prices adjusted for splits and
  dividends as of the day they were fetched; the live plan saw unadjusted ones.
  Every level is derived from the same adjusted series, so R is unaffected.
* **Idealised fills.** Same slippage as the paper account and nothing more: no
  partial fills, no liquidity limit, no overnight gap beyond what the bars show.
  Hypothetical trades are also never constrained by cash, caps or the sector limit.
* **Stale data is acceptable.** This is analysis, not a trading decision, so it does
  not use `fresh_data_only()`; the history store serves what it holds.

Computing happens on a refresh (POST, or the daily job), never on a GET. A result
that reached a stop, a target or the time limit is final and is never recomputed;
one still open, or one whose prices could not be loaded, is recomputed on later
refreshes (a bounded number per run).
"""

from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

import pandas as pd
from sqlmodel import Session, select

from app.analysis.indicators import latest_atr
from app.analysis.trend import analyze_chart
from app.config import AppSettings
from app.markets import is_always_on, is_daily_bar_final, to_market_time, us_session_bounds
from app.portfolio.engine import PaperTradingEngine
from app.portfolio.missed_trade_models import (
    OUTCOME_NO_DATA,
    OUTCOME_NOT_SIMULATABLE,
    OUTCOME_OPEN,
    OUTCOME_RESOLVED,
    MissedTradeOutcome,
)
from app.portfolio.intraday import RESOLUTION_DAILY
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.risk.position_sizing import calculate_position_size, derive_targets
from app.services.trade_plan_service import ATR_PERIOD, _derive_entry_and_stop
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------- categories

CATEGORY_LOW_CONFIDENCE = "low_confidence"  # no_trade: the evidence score was under the bar
CATEGORY_AI_VETO = "ai_veto"  # no_trade: the AI Trading Overlay objected ("cancel")
CATEGORY_NEUTRAL_TREND = "neutral_trend"  # no_trade: no trend, so no direction to simulate
CATEGORY_HELD_BY_AI = "held_by_ai"  # a plan was written and left pending by the overlay ("hold")
CATEGORY_NOT_EXECUTED = "not_executed"  # a plan was written and never filled (see `detail`)
CATEGORY_UNCLASSIFIED = "unclassified"  # a non-executed plan whose text matches none of the above

CATEGORIES: tuple[str, ...] = (
    CATEGORY_AI_VETO,
    CATEGORY_LOW_CONFIDENCE,
    CATEGORY_HELD_BY_AI,
    CATEGORY_NOT_EXECUTED,
    CATEGORY_NEUTRAL_TREND,
    CATEGORY_UNCLASSIFIED,
)

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_AI_VETO: "AI veto",
    CATEGORY_LOW_CONFIDENCE: "Under the confidence bar",
    CATEGORY_HELD_BY_AI: "Held by the AI",
    CATEGORY_NOT_EXECUTED: "Written, never filled",
    CATEGORY_NEUTRAL_TREND: "No trend",
    CATEGORY_UNCLASSIFIED: "Other",
}

# `detail` for CATEGORY_NOT_EXECUTED: why the engine did not fill the plan.
DETAIL_INSUFFICIENT_CASH = "insufficient_cash"
DETAIL_POSITION_CAP = "position_cap"
DETAIL_SECTOR_CAP = "sector_cap"
DETAIL_STALE_PRICE = "stale_price"
DETAIL_DUPLICATE_POSITION = "duplicate_position"
DETAIL_MARKET_CLOSED_REDO = "market_closed_awaiting_redo"
DETAIL_AWAITING_MANUAL = "awaiting_manual"
DETAIL_OTHER_REFUSAL = "other_refusal"

DETAIL_LABELS: dict[str, str] = {
    DETAIL_INSUFFICIENT_CASH: "Not enough cash for one share",
    DETAIL_POSITION_CAP: "At the position cap",
    DETAIL_SECTOR_CAP: "At the sector cap",
    DETAIL_STALE_PRICE: "Price had moved too far from the plan",
    DETAIL_DUPLICATE_POSITION: "Symbol already held",
    DETAIL_MARKET_CLOSED_REDO: "Market closed: waiting for the redo at the open",
    DETAIL_AWAITING_MANUAL: "Pending: auto-execute not attempted",
    DETAIL_OTHER_REFUSAL: "Refused by the engine",
}

# The exact beginnings of the strings trade_plan_service / automation_service write.
# Matched by prefix (and for the engine's refusals, a stable phrase of the
# exception text) in ONE place so a reworded message shows up as a failing test
# (tests/test_missed_trades.py runs the real generator) rather than as plans
# silently drifting into "unclassified".
REASON_NO_TREND_PREFIX = "No clear trend"
REASON_AI_VETO_PREFIX = "AI Trading Overlay"
REASON_LOW_CONFIDENCE_PREFIX = "Confidence too low"
NOTE_HELD_BY_AI_PREFIX = "Auto-execute held: AI Trading Overlay"
NOTE_REFUSED_PREFIX = "Auto-execute skipped:"
NOTE_MARKET_CLOSED_PREFIX = "Market closed ("

# Phrases inside the engine's refusal messages (portfolio/engine.py), in the order
# they are tested.
_REFUSAL_PHRASES: tuple[tuple[str, str], ...] = (
    ("already has an open position", DETAIL_DUPLICATE_POSITION),
    ("-position cap", DETAIL_POSITION_CAP),
    ("-per-sector cap", DETAIL_SECTOR_CAP),
    ("can't afford 1 share", DETAIL_INSUFFICIENT_CASH),
    ("past the", DETAIL_STALE_PRICE),
)

_DIRECTION_IN_VETO = re.compile(r"rule-based (long|short)")
_TREND_IN_LOW_CONFIDENCE = re.compile(r"despite a (bullish|bearish) trend")


@dataclass(frozen=True)
class MissedClassification:
    category: str
    detail: str | None
    # Whether a hypothetical trade can and should be simulated for this plan.
    simulate: bool
    label: str


def classify_plan(plan: TradePlanRecord) -> MissedClassification | None:
    """Which kind of missed trade `plan` is, or None when it is not one: executed
    plans are the real record, and a discarded plan was superseded by a newer one
    (a regenerated plan, or the fresh plan an off-hours redo made), which is the
    decision that counts. Matches the strings the live code writes, nothing looser."""
    if plan.status == "no_trade":
        reason = (plan.reason or "").strip()
        if reason.startswith(REASON_NO_TREND_PREFIX):
            return MissedClassification(CATEGORY_NEUTRAL_TREND, None, False, CATEGORY_LABELS[CATEGORY_NEUTRAL_TREND])
        if reason.startswith(REASON_AI_VETO_PREFIX):
            return MissedClassification(CATEGORY_AI_VETO, None, True, CATEGORY_LABELS[CATEGORY_AI_VETO])
        if reason.startswith(REASON_LOW_CONFIDENCE_PREFIX):
            return MissedClassification(CATEGORY_LOW_CONFIDENCE, None, True, CATEGORY_LABELS[CATEGORY_LOW_CONFIDENCE])
        return MissedClassification(CATEGORY_UNCLASSIFIED, None, False, CATEGORY_LABELS[CATEGORY_UNCLASSIFIED])

    if plan.status == "pending":
        note = (plan.auto_execute_note or "").strip()
        if note.startswith(NOTE_HELD_BY_AI_PREFIX):
            return MissedClassification(CATEGORY_HELD_BY_AI, None, True, CATEGORY_LABELS[CATEGORY_HELD_BY_AI])
        if note.startswith(NOTE_MARKET_CLOSED_PREFIX):
            # Waiting for its redo: the fresh plan the redo makes is the decision, and this
            # one is retired when it does, so simulating both would count one idea twice.
            return _not_executed(DETAIL_MARKET_CLOSED_REDO, simulate=False)
        if note.startswith(NOTE_REFUSED_PREFIX):
            for phrase, detail in _REFUSAL_PHRASES:
                if phrase in note:
                    # A duplicate is a second plan for something already held: the position
                    # exists, so simulating it would count that one idea twice.
                    return _not_executed(detail, simulate=detail != DETAIL_DUPLICATE_POSITION)
            return _not_executed(DETAIL_OTHER_REFUSAL, simulate=True)
        if not note:
            # No auto-execute attempt was made (the setting is off, or the position cap
            # stopped the auto-scan before it tried). Still a plan nobody acted on.
            return _not_executed(DETAIL_AWAITING_MANUAL, simulate=True)
        return MissedClassification(CATEGORY_UNCLASSIFIED, None, False, CATEGORY_LABELS[CATEGORY_UNCLASSIFIED])
    return None


def _not_executed(detail: str, *, simulate: bool) -> MissedClassification:
    return MissedClassification(CATEGORY_NOT_EXECUTED, detail, simulate, DETAIL_LABELS[detail])


# ---------------------------------------------------------------- the bars

# Daily bars the live evaluation analyses: one year (trade_plan_service fetches
# period="1y"). Calendar days, so a plan's analysis window is the year before it.
ANALYSIS_LOOKBACK_DAYS = 365
# Fewer bars than this and EMA50 / the trend say little: no trade is rebuilt.
MIN_BARS_FOR_RECONSTRUCTION = 60
# Bars older than this many days before a moment are final by construction, so
# the (calendar-maths) finality check only runs on the recent ones.
_FINALITY_RECENT_DAYS = 7

SIM_TRADE_SOURCE_PLAN = "plan"
SIM_TRADE_SOURCE_RECONSTRUCTED = "reconstructed"
DIRECTION_FROM_PLAN = "plan"
DIRECTION_FROM_REASON = "reason"
DIRECTION_FROM_TREND = "trend"
RESOLUTION_DAILY_ONLY = "daily"

# (symbol, start) -> daily bars (columns date, open, high, low, close, volume),
# everything from `start` to the latest final bar.
BarsLoader = Callable[[str, date], pd.DataFrame]


class MissedTradeDataError(Exception):
    """The price history for a symbol could not be loaded."""


def history_store_bars_loader(symbol: str, start: date) -> pd.DataFrame:
    """The default loader: the local price-history store (final daily bars only,
    downloaded once and kept). Fails with MissedTradeDataError when nothing can
    be had, so the caller records "no data" instead of a guess."""
    from app.data_providers.history_store import HistoryUnavailableError, get_history_store

    try:
        return get_history_store().get_daily_history(symbol, start=start)
    except HistoryUnavailableError as exc:
        raise MissedTradeDataError(str(exc)) from exc


def _clean_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Daily bars with tz-naive midnight dates, ascending, one row per day."""
    if frame is None or frame.empty or "date" not in frame.columns:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    out = frame.copy()
    dates = pd.to_datetime(out["date"])
    if dates.dt.tz is not None:
        dates = dates.dt.tz_localize(None)
    out["date"] = dates.dt.normalize()
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out = out.dropna(subset=["open", "high", "low", "close"])
    return out.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)


def _final_as_of(symbol: str, bars: pd.DataFrame, moment: datetime) -> pd.Series:
    """For each bar: was it final at `moment` (naive UTC)? A bar dated after
    `moment` can never be, and one stored before the last week is final by
    construction."""
    recent_from = pd.Timestamp(moment.date() - timedelta(days=_FINALITY_RECENT_DAYS))
    return pd.Series(
        [
            True if ts < recent_from else is_daily_bar_final(symbol, ts.date(), moment)
            for ts in bars["date"]
        ],
        index=bars.index,
        dtype=bool,
    )


def bars_known_at(symbol: str, bars: pd.DataFrame, decided_at: datetime) -> pd.DataFrame:
    """The daily bars that were final when the decision was made: the only ones its
    evaluation could have seen as finished. A bar stamped with the day the decision
    was taken is excluded unless that day's session had already ended."""
    if bars.empty:
        return bars
    return bars[_final_as_of(symbol, bars, decided_at)].reset_index(drop=True)


def _decision_day_started(symbol: str, decided_at: datetime) -> tuple[date, bool]:
    """(the calendar day of the decision, whether that day's bar was already forming)."""
    if is_always_on(symbol):
        return decided_at.date(), True
    moment = to_market_time(decided_at)
    bounds = us_session_bounds(moment.date())
    return moment.date(), bounds is not None and moment >= bounds.open


def bars_after_decision(symbol: str, bars: pd.DataFrame, known: pd.DataFrame, decided_at: datetime, now: datetime) -> pd.DataFrame:
    """The bars the hypothetical trade lives through: after the last known bar (its
    close is the entry), skipping the day of the decision when it was already
    under way (the live engine never lets the entry bar trigger an exit), and
    only bars that are final at `now`."""
    if bars.empty or known.empty:
        return bars.iloc[0:0]
    entry_day = known["date"].iloc[-1]
    after = bars[bars["date"] > entry_day]
    day, started = _decision_day_started(symbol, decided_at)
    if started:
        after = after[after["date"] != pd.Timestamp(day)]
    if after.empty:
        return after.reset_index(drop=True)
    return after[_final_as_of(symbol, after.reset_index(drop=True), now).values].reset_index(drop=True)


# ----------------------------------------------------------- the simulation


@dataclass
class SimulatedOutcome:
    """What one hypothetical trade did. `status` is one of the OUTCOME_* values."""

    status: str
    direction: str | None = None
    entry: float | None = None
    fill_price: float | None = None
    stop: float | None = None
    tp1: float | None = None
    trade_source: str | None = None
    direction_source: str | None = None
    r_multiple: float | None = None
    exit_reason: str | None = None
    exit_price: float | None = None
    exit_date: date | None = None
    mark_price: float | None = None
    entry_bar_date: date | None = None
    bars_known: int | None = None
    bars_walked: int | None = None
    resolution: str | None = RESOLUTION_DAILY_ONLY
    note: str | None = None


def _direction_from_text(plan: TradePlanRecord) -> str | None:
    """A no-trade decision stores no direction, but the reason names it: the veto
    says "against a rule-based long", the confidence bar "despite a bullish trend".
    That is the direction the live evaluation had, so it beats re-deriving it from
    a chart that may differ by a partial bar."""
    reason = plan.reason or ""
    match = _DIRECTION_IN_VETO.search(reason)
    if match:
        return match.group(1)
    match = _TREND_IN_LOW_CONFIDENCE.search(reason)
    if match:
        return "long" if match.group(1) == "bullish" else "short"
    return None


def _r_multiple(
    direction: str, fill: float, exit_price: float, stop: float, shares: int, commission_per_trade: float
) -> float | None:
    """R the way close_position computes it: net result over the money risked, with
    the commission charged at both ends. None when the stop distance is zero."""
    risk_per_share = abs(fill - stop)
    if risk_per_share <= 0:
        return None
    sign = 1 if direction == "long" else -1
    per_share = (exit_price - fill) * sign
    if shares > 0:
        per_share -= (2 * commission_per_trade) / shares
    return per_share / risk_per_share


def simulate_missed_trade(
    plan: TradePlanRecord,
    classification: MissedClassification,
    bars: pd.DataFrame,
    settings: AppSettings,
    now: datetime,
) -> SimulatedOutcome:
    """The whole counterfactual for one plan. `bars` is the symbol's full daily history
    (any amount past the decision is fine: nothing after the decision reaches the
    reconstruction, only the walk). Pure: no database, no network."""
    symbol = plan.symbol
    bars = _clean_bars(bars)
    known = bars_known_at(symbol, bars, plan.created_at)
    if known.empty:
        return SimulatedOutcome(OUTCOME_NO_DATA, note="No price history before the decision.", bars_known=0)

    window_start = known["date"].iloc[-1] - pd.Timedelta(days=ANALYSIS_LOOKBACK_DAYS)
    analysis_bars = known[known["date"] > window_start].reset_index(drop=True)
    entry_bar_date = known["date"].iloc[-1].date()

    # Direction and levels.
    has_plan_levels = all(v is not None for v in (plan.direction, plan.entry, plan.stop, plan.tp1))
    if has_plan_levels:
        direction = plan.direction
        entry, stop, tp1 = float(plan.entry), float(plan.stop), float(plan.tp1)
        tp2 = float(plan.tp2) if plan.tp2 is not None else tp1
        trade_source, direction_source = SIM_TRADE_SOURCE_PLAN, DIRECTION_FROM_PLAN
    else:
        if len(analysis_bars) < MIN_BARS_FOR_RECONSTRUCTION:
            return SimulatedOutcome(
                OUTCOME_NO_DATA,
                note=f"Only {len(analysis_bars)} bars before the decision; {MIN_BARS_FOR_RECONSTRUCTION} are needed.",
                bars_known=len(analysis_bars),
                entry_bar_date=entry_bar_date,
            )
        chart = analyze_chart(analysis_bars)
        direction = _direction_from_text(plan)
        direction_source = DIRECTION_FROM_REASON
        if direction is None:
            direction = "long" if chart.trend == "Bullish" else "short" if chart.trend == "Bearish" else None
            direction_source = DIRECTION_FROM_TREND
        if direction is None:
            return SimulatedOutcome(
                OUTCOME_NOT_SIMULATABLE,
                note="No trend on the bars known at the decision, so no direction to simulate.",
                bars_known=len(analysis_bars),
                entry_bar_date=entry_bar_date,
            )
        atr_value = latest_atr(analysis_bars, ATR_PERIOD)
        entry, stop = _derive_entry_and_stop(direction, chart.price, chart.support, chart.resistance, atr_value)
        targets = derive_targets(entry, stop, direction, chart.support, chart.resistance)
        tp1, tp2 = targets.tp1, targets.tp2
        trade_source = SIM_TRADE_SOURCE_RECONSTRUCTED

    if abs(entry - stop) <= 0:
        return SimulatedOutcome(
            OUTCOME_NOT_SIMULATABLE, note="The stop sits on the entry price.", bars_known=len(analysis_bars),
            entry_bar_date=entry_bar_date,
        )

    # Hypothetical position, never added to a session: only the engine's exit scan
    # reads it. The engine is built just for its slippage and its exit rules.
    engine = PaperTradingEngine(
        None,  # type: ignore[arg-type]  # no database is touched
        None,  # type: ignore[arg-type]  # no provider is touched
        slippage_bps=settings.slippage_bps,
        commission_per_trade=settings.commission_per_trade,
        clock=lambda: now,
        max_holding_days=settings.max_holding_days,
        intraday_exits=False,
    )
    fill_price = engine._slip(entry, buying=direction == "long")
    shares = plan.suggested_shares or calculate_position_size(
        settings.paper_starting_cash, settings.default_risk_pct, entry, stop
    ).shares
    position = PaperPosition(
        symbol=symbol,
        direction=direction,
        entry_price=fill_price,
        stop_loss=stop,
        tp1=tp1,
        tp2=tp2,
        shares=shares,
        opened_at=plan.created_at,
    )

    walk = bars_after_decision(symbol, bars, known, plan.created_at, now)
    found = engine._scan_exit(position, walk, holding_limit=engine._max_holding_days)

    common = dict(
        direction=direction,
        entry=entry,
        fill_price=fill_price,
        stop=stop,
        tp1=tp1,
        trade_source=trade_source,
        direction_source=direction_source,
        entry_bar_date=entry_bar_date,
        bars_known=len(analysis_bars),
    )
    if found is not None:
        exit_date = walk["date"].iloc[found.bars_walked - 1].date()
        return SimulatedOutcome(
            OUTCOME_RESOLVED,
            r_multiple=_r_multiple(direction, fill_price, found.fill_price, stop, shares, settings.commission_per_trade),
            exit_reason=found.reason,
            exit_price=found.fill_price,
            exit_date=exit_date,
            bars_walked=found.bars_walked,
            resolution=RESOLUTION_DAILY_ONLY,
            note=(
                "Both levels fell inside one daily bar: the stop was taken."
                if found.resolution != RESOLUTION_DAILY
                else None
            ),
            **common,
        )
    if walk.empty:
        return SimulatedOutcome(OUTCOME_OPEN, bars_walked=0, note="No full trading day has passed since the decision.", **common)
    mark = float(walk["close"].iloc[-1])
    return SimulatedOutcome(
        OUTCOME_OPEN,
        r_multiple=_r_multiple(direction, fill_price, mark, stop, shares, settings.commission_per_trade),
        mark_price=mark,
        bars_walked=len(walk),
        note="Still open: marked at the latest close, not a final result.",
        **common,
    )


# --------------------------------------------------------------- refreshing

# A refresh computes at most this many plans (new ones first, then the oldest
# unresolved), so one click or one scheduled run stays bounded however much
# history has piled up. Whatever is left is picked up by the next run.
REFRESH_MAX_PLANS = 200

_refresh_lock = threading.Lock()


@dataclass
class RefreshSummary:
    considered: int = 0  # plans with a hypothetical trade to compute (new or unresolved)
    computed: int = 0
    resolved: int = 0
    still_open: int = 0
    no_data: int = 0
    not_simulatable: int = 0
    skipped_resolved: int = 0  # already final: never recomputed
    remaining: int = 0  # left for the next refresh (over the per-run bound)
    busy: bool = False  # another refresh was already running: nothing was done
    failed_symbols: list[str] = field(default_factory=list)


def _apply(row: MissedTradeOutcome, category: str, outcome: SimulatedOutcome, computed_at: datetime) -> None:
    row.category = category
    row.status = outcome.status
    for name in (
        "direction", "entry", "fill_price", "stop", "tp1", "trade_source", "direction_source", "r_multiple",
        "exit_reason", "exit_price", "exit_date", "mark_price", "entry_bar_date", "bars_known", "bars_walked",
        "resolution", "note",
    ):
        setattr(row, name, getattr(outcome, name))
    row.computed_at = computed_at


def refresh_missed_trades(
    session: Session,
    settings: AppSettings,
    load_bars: BarsLoader = history_store_bars_loader,
    *,
    now: datetime | None = None,
    max_plans: int = REFRESH_MAX_PLANS,
) -> RefreshSummary:
    """Compute (or recompute) the stored outcomes. Never recomputes a final one. One
    price-history load per symbol per run. A symbol whose history cannot be loaded
    is recorded as "no data" and retried next time, never guessed at. A second
    refresh while one is running does nothing (the table has one row per plan, and
    two writers would only race for it)."""
    summary = RefreshSummary()
    if not _refresh_lock.acquire(blocking=False):
        summary.busy = True
        return summary
    try:
        now = now or utcnow_naive()
        plans = session.exec(
            select(TradePlanRecord).where(TradePlanRecord.status.in_(("pending", "no_trade")))
        ).all()
        rows = {row.plan_id: row for row in session.exec(select(MissedTradeOutcome)).all()}

        fresh: list[tuple[TradePlanRecord, MissedClassification]] = []
        stale: list[tuple[TradePlanRecord, MissedClassification]] = []
        for plan in plans:
            classification = classify_plan(plan)
            if classification is None or not classification.simulate:
                continue
            row = rows.get(plan.id)
            if row is None:
                fresh.append((plan, classification))
            elif row.status in (OUTCOME_OPEN, OUTCOME_NO_DATA):
                stale.append((plan, classification))
            else:
                summary.skipped_resolved += 1
        fresh.sort(key=lambda pair: pair[0].created_at, reverse=True)
        stale.sort(key=lambda pair: rows[pair[0].id].computed_at)
        todo = fresh + stale
        summary.considered = len(todo)
        summary.remaining = max(0, len(todo) - max_plans)
        todo = todo[:max_plans]

        by_symbol: dict[str, list[tuple[TradePlanRecord, MissedClassification]]] = defaultdict(list)
        for pair in todo:
            by_symbol[pair[0].symbol].append(pair)

        for symbol, pairs in by_symbol.items():
            start = min(p.created_at for p, _ in pairs).date() - timedelta(days=ANALYSIS_LOOKBACK_DAYS + 10)
            bars: pd.DataFrame | None
            error = ""
            try:
                bars = load_bars(symbol, start)
            except Exception as exc:  # a provider failure must not abort the other symbols
                logger.warning("Missed trades: no price history for %s: %s", symbol, exc)
                bars, error = None, str(exc) or exc.__class__.__name__
                summary.failed_symbols.append(symbol)
            for plan, classification in pairs:
                if bars is None:
                    outcome = SimulatedOutcome(OUTCOME_NO_DATA, note=f"Price history unavailable: {error}"[:500])
                else:
                    try:
                        outcome = simulate_missed_trade(plan, classification, bars, settings, now)
                    except Exception as exc:  # one malformed plan must not stop the rest
                        logger.exception("Missed trades: could not simulate plan %s", plan.id)
                        outcome = SimulatedOutcome(OUTCOME_NO_DATA, note=f"Simulation failed: {exc}"[:500])
                row = rows.get(plan.id) or MissedTradeOutcome(plan_id=plan.id, symbol=symbol, category=classification.category)
                _apply(row, classification.category, outcome, now)
                session.add(row)
                rows[plan.id] = row
                summary.computed += 1
                if outcome.status == OUTCOME_RESOLVED:
                    summary.resolved += 1
                elif outcome.status == OUTCOME_OPEN:
                    summary.still_open += 1
                elif outcome.status == OUTCOME_NO_DATA:
                    summary.no_data += 1
                else:
                    summary.not_simulatable += 1
            session.commit()
        return summary
    finally:
        _refresh_lock.release()
