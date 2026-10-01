"""The missed-trades report: what the trades we did not take earned, next to the ones we did.

Read-only, like portfolio/calibration.py: it classifies the plans on file, joins
the stored counterfactual outcomes (missed_trades.refresh_missed_trades computes
them) and the closed positions, and never writes, marks to market or fetches a
price. Opening the page therefore changes nothing; the Refresh button (a POST) and
the daily job do the computing.

Honesty rules, the same as the calibration report's:
- Every statistic comes with n and an interval (Wilson for a win rate, bootstrap
  for an average R). Below MIN_TRADES_FOR_READING resolved trades the headline
  questions say it is too early instead of giving a verdict.
- Nothing is dropped silently: plans still to be computed, trades still open, and
  plans that cannot be simulated (no trend, a duplicate of a held position, a plan
  waiting for its redo) are each counted and shown.
- Hypothetical fills are idealised; the caveats are part of the payload, not a
  footnote the page can forget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlmodel import Session, select

from app.portfolio.calibration import MIN_TRADES_FOR_READING
from app.portfolio.missed_trade_models import (
    OUTCOME_NO_DATA,
    OUTCOME_NOT_SIMULATABLE,
    OUTCOME_OPEN,
    OUTCOME_RESOLVED,
    MissedTradeOutcome,
)
from app.portfolio.missed_trades import (
    CATEGORIES,
    CATEGORY_AI_VETO,
    CATEGORY_LABELS,
    CATEGORY_LOW_CONFIDENCE,
    DETAIL_LABELS,
    MissedClassification,
    classify_plan,
)
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.portfolio.signal_stats import bootstrap_mean_interval, wilson_interval

# The per-trade list is the newest this many; the totals above it count everything.
MAX_LISTED_TRADES = 300

# Row states shown in the list.
STATE_RESOLVED = "resolved"
STATE_OPEN = "open"
STATE_AWAITING = "awaiting"  # simulatable, but no result stored yet (refresh needed)
STATE_NOT_SIMULATED = "not_simulated"

VERDICT_TOO_EARLY = "too_early"
VERDICT_NO_DATA = "no_data"
VERDICT_UNCLEAR = "unclear"
# AI veto question
VERDICT_SAVED = "saved"
VERDICT_COST = "cost"
# Confidence-bar question
VERDICT_BAR_JUSTIFIED = "bar_justified"
VERDICT_BAR_COSTS = "bar_costs"

CAVEATS: tuple[str, ...] = (
    "These are hypothetical trades, not results. Each one is filled at the last daily close known when the plan was "
    "made, with the paper account's slippage and commission, and nothing else.",
    "They are walked on daily bars only. The live engine also reads hourly bars to order a stop and a target inside one "
    "day; here a day that touches both counts as the stop. That can understate the hypothetical result, never flatter it.",
    "Levels for a no-trade decision are rebuilt with today's rules from the bars known then, not the exact numbers the "
    "decision saw (the quote it used is not stored).",
    "Hypothetical trades ignore cash, the position cap and the sector cap, so they can overlap in ways the real account "
    "could not. Several of them on one symbol are one idea counted more than once.",
    "Hindsight and survivorship: the prices come from symbols that still trade, and a small sample can look like a "
    "pattern. Read a category only when it has enough resolved trades; the ranges say how wide the doubt is.",
)


@dataclass
class DetailCount:
    detail: str
    label: str
    n: int


@dataclass
class CategoryStats:
    key: str
    label: str
    n: int  # plans in this category
    resolved: int  # hypothetical trades that reached a stop, TP1 or the time limit
    open: int  # still open (marked, not final)
    awaiting: int  # to be computed: Refresh needed (or the price history was unavailable)
    not_simulated: int  # counted but not simulated (no trend, duplicate, waiting for its redo)
    wins: int
    win_rate: float | None  # percent, of resolved
    win_rate_low: float | None  # Wilson 95% interval, percent
    win_rate_high: float | None
    avg_r: float | None  # of resolved
    avg_r_low: float | None  # bootstrap 95% interval
    avg_r_high: float | None
    total_r: float  # of resolved
    open_avg_r: float | None  # mean mark of the open ones: not a result
    small_sample: bool
    details: list[DetailCount] = field(default_factory=list)


@dataclass
class TakenStats:
    n: int
    wins: int
    win_rate: float | None
    win_rate_low: float | None
    win_rate_high: float | None
    avg_r: float | None
    avg_r_low: float | None
    avg_r_high: float | None
    total_r: float
    small_sample: bool


@dataclass
class MissedQuestion:
    key: str
    question: str
    verdict: str
    answer: str


@dataclass
class MissedTradeItem:
    plan_id: int
    symbol: str
    created_at: datetime
    category: str
    category_label: str
    detail: str | None
    detail_label: str | None
    reason: str | None
    state: str
    direction: str | None
    confidence_score: int
    entry: float | None
    stop: float | None
    tp1: float | None
    r_multiple: float | None  # final when state == "resolved", a mark when "open"
    exit_reason: str | None
    exit_date: date | None
    trade_source: str | None
    note: str | None


@dataclass
class MissedTradeReport:
    plans_considered: int
    awaiting_refresh: int
    min_trades_for_reading: int
    last_computed_at: datetime | None
    categories: list[CategoryStats]
    taken: TakenStats
    questions: list[MissedQuestion]
    trades: list[MissedTradeItem]
    trades_listed: int
    caveats: list[str]


def _r_stats(values: list[float]) -> tuple[int, float | None, float | None, float | None, float]:
    """(wins, avg, avg_low, avg_high, total) of a list of R values; a win is R above zero."""
    if not values:
        return 0, None, None, None, 0.0
    interval = bootstrap_mean_interval(values)
    return (
        sum(1 for r in values if r > 0),
        sum(values) / len(values),
        interval[0] if interval else None,
        interval[1] if interval else None,
        sum(values),
    )


def _win_rate(wins: int, n: int) -> tuple[float | None, float | None, float | None]:
    wilson = wilson_interval(wins, n)
    if not n:
        return None, None, None
    return wins / n * 100, wilson[0] * 100 if wilson else None, wilson[1] * 100 if wilson else None


def _fmt_r(value: float) -> str:
    return f"{value:+.2f}R"


def taken_stats(session: Session) -> TakenStats:
    """The real closed trades, for the head-to-head. Same R the Portfolio page uses."""
    r_values = [
        r
        for r in session.exec(
            select(PaperPosition.realized_r).where(PaperPosition.status == "closed", PaperPosition.realized_r.is_not(None))
        ).all()
        if r is not None
    ]
    wins, avg, low, high, total = _r_stats(r_values)
    rate, rate_low, rate_high = _win_rate(wins, len(r_values))
    return TakenStats(
        n=len(r_values), wins=wins, win_rate=rate, win_rate_low=rate_low, win_rate_high=rate_high,
        avg_r=avg, avg_r_low=low, avg_r_high=high, total_r=total, small_sample=len(r_values) < MIN_TRADES_FOR_READING,
    )


def _state_of(classification: MissedClassification, row: MissedTradeOutcome | None) -> str:
    if not classification.simulate:
        return STATE_NOT_SIMULATED
    if row is None or row.status == OUTCOME_NO_DATA:
        return STATE_AWAITING
    if row.status == OUTCOME_RESOLVED:
        return STATE_RESOLVED
    if row.status == OUTCOME_OPEN:
        return STATE_OPEN
    if row.status == OUTCOME_NOT_SIMULATABLE:
        return STATE_NOT_SIMULATED
    return STATE_AWAITING


def _veto_question(stats: CategoryStats) -> MissedQuestion:
    question = "Did the AI veto save or cost money?"
    if stats.resolved == 0:
        return MissedQuestion(
            "ai_veto", question, VERDICT_NO_DATA,
            "No vetoed trade has a result yet." + (" Press Refresh to compute them." if stats.awaiting else ""),
        )
    base = f"{stats.resolved} vetoed trades resolved, averaging {_fmt_r(stats.avg_r)} each ({_fmt_r(stats.total_r)} in all)."
    if stats.resolved < MIN_TRADES_FOR_READING:
        return MissedQuestion(
            "ai_veto", question, VERDICT_TOO_EARLY,
            f"{base} Too early to tell: about {MIN_TRADES_FOR_READING} are needed before this means anything.",
        )
    if stats.avg_r_high is not None and stats.avg_r_high < 0:
        return MissedQuestion(
            "ai_veto", question, VERDICT_SAVED,
            f"{base} The whole range is below zero: the veto kept about {_fmt_r(-stats.total_r)} out of the account.",
        )
    if stats.avg_r_low is not None and stats.avg_r_low > 0:
        return MissedQuestion(
            "ai_veto", question, VERDICT_COST,
            f"{base} The whole range is above zero: the veto cost about {_fmt_r(stats.total_r)} of trades that would have worked.",
        )
    return MissedQuestion("ai_veto", question, VERDICT_UNCLEAR, f"{base} The range spans zero: no clear effect either way.")


def _bar_question(stats: CategoryStats, taken: TakenStats) -> MissedQuestion:
    question = "Are the plans under the confidence bar worse than the ones taken?"
    if stats.resolved == 0:
        return MissedQuestion(
            "confidence_bar", question, VERDICT_NO_DATA,
            "No plan under the bar has a result yet." + (" Press Refresh to compute them." if stats.awaiting else ""),
        )
    if taken.n == 0:
        return MissedQuestion(
            "confidence_bar", question, VERDICT_NO_DATA,
            f"No closed trades to compare with yet. The {stats.resolved} resolved plans under the bar averaged "
            f"{_fmt_r(stats.avg_r)}.",
        )
    base = (
        f"Under the bar: {_fmt_r(stats.avg_r)} on average over {stats.resolved} resolved. "
        f"Taken: {_fmt_r(taken.avg_r)} over {taken.n} closed."
    )
    if stats.resolved < MIN_TRADES_FOR_READING or taken.n < MIN_TRADES_FOR_READING:
        return MissedQuestion(
            "confidence_bar", question, VERDICT_TOO_EARLY,
            f"{base} Too early to tell: about {MIN_TRADES_FOR_READING} of each are needed.",
        )
    if None in (stats.avg_r_high, stats.avg_r_low, taken.avg_r_high, taken.avg_r_low):
        return MissedQuestion("confidence_bar", question, VERDICT_UNCLEAR, f"{base} No clear difference.")
    if stats.avg_r_high < taken.avg_r_low:
        return MissedQuestion(
            "confidence_bar", question, VERDICT_BAR_JUSTIFIED,
            f"{base} The ranges do not overlap: plans under the bar did worse, so the bar is earning its keep.",
        )
    if stats.avg_r_low > taken.avg_r_high:
        return MissedQuestion(
            "confidence_bar", question, VERDICT_BAR_COSTS,
            f"{base} The ranges do not overlap: plans under the bar did better than the ones taken, so the bar may be too strict.",
        )
    return MissedQuestion("confidence_bar", question, VERDICT_UNCLEAR, f"{base} The ranges overlap: no clear difference.")


def build_missed_trade_report(session: Session) -> MissedTradeReport:
    plans = session.exec(
        select(TradePlanRecord)
        .where(TradePlanRecord.status.in_(("pending", "no_trade")))
        .order_by(TradePlanRecord.created_at.desc())
    ).all()
    rows = {row.plan_id: row for row in session.exec(select(MissedTradeOutcome)).all()}

    per_category: dict[str, list[tuple[TradePlanRecord, MissedClassification, MissedTradeOutcome | None, str]]] = {
        key: [] for key in CATEGORIES
    }
    for plan in plans:
        classification = classify_plan(plan)
        if classification is None:
            continue
        row = rows.get(plan.id)
        per_category[classification.category].append((plan, classification, row, _state_of(classification, row)))

    categories: list[CategoryStats] = []
    items: list[MissedTradeItem] = []
    for key in CATEGORIES:
        entries = per_category[key]
        resolved_r = [row.r_multiple for _, _, row, state in entries if state == STATE_RESOLVED and row.r_multiple is not None]
        open_r = [row.r_multiple for _, _, row, state in entries if state == STATE_OPEN and row.r_multiple is not None]
        wins, avg, low, high, total = _r_stats(resolved_r)
        rate, rate_low, rate_high = _win_rate(wins, len(resolved_r))
        detail_counts: dict[str, int] = {}
        for _, classification, _, _ in entries:
            if classification.detail:
                detail_counts[classification.detail] = detail_counts.get(classification.detail, 0) + 1
        categories.append(
            CategoryStats(
                key=key,
                label=CATEGORY_LABELS[key],
                n=len(entries),
                resolved=len(resolved_r),
                open=sum(1 for *_, state in entries if state == STATE_OPEN),
                awaiting=sum(1 for *_, state in entries if state == STATE_AWAITING),
                not_simulated=sum(1 for *_, state in entries if state == STATE_NOT_SIMULATED),
                wins=wins,
                win_rate=rate,
                win_rate_low=rate_low,
                win_rate_high=rate_high,
                avg_r=avg,
                avg_r_low=low,
                avg_r_high=high,
                total_r=total,
                open_avg_r=sum(open_r) / len(open_r) if open_r else None,
                small_sample=len(resolved_r) < MIN_TRADES_FOR_READING,
                details=[DetailCount(d, DETAIL_LABELS[d], n) for d, n in sorted(detail_counts.items())],
            )
        )
        for plan, classification, row, state in entries:
            has_result = state in (STATE_RESOLVED, STATE_OPEN) and row is not None
            items.append(
                MissedTradeItem(
                    plan_id=plan.id,
                    symbol=plan.symbol,
                    created_at=plan.created_at,
                    category=key,
                    category_label=CATEGORY_LABELS[key],
                    detail=classification.detail,
                    detail_label=DETAIL_LABELS.get(classification.detail) if classification.detail else None,
                    reason=(plan.reason or plan.auto_execute_note or None),
                    state=state,
                    direction=(row.direction if has_result else plan.direction),
                    confidence_score=plan.confidence_score,
                    entry=row.entry if has_result else plan.entry,
                    stop=row.stop if has_result else plan.stop,
                    tp1=row.tp1 if has_result else plan.tp1,
                    r_multiple=row.r_multiple if has_result else None,
                    exit_reason=row.exit_reason if has_result else None,
                    exit_date=row.exit_date if has_result else None,
                    trade_source=row.trade_source if has_result else None,
                    note=row.note if row is not None else None,
                )
            )
    items.sort(key=lambda item: item.created_at, reverse=True)

    by_key = {c.key: c for c in categories}
    taken = taken_stats(session)
    computed = [row.computed_at for row in rows.values()]
    return MissedTradeReport(
        plans_considered=sum(c.n for c in categories),
        awaiting_refresh=sum(c.awaiting for c in categories),
        min_trades_for_reading=MIN_TRADES_FOR_READING,
        last_computed_at=max(computed) if computed else None,
        categories=categories,
        taken=taken,
        questions=[_veto_question(by_key[CATEGORY_AI_VETO]), _bar_question(by_key[CATEGORY_LOW_CONFIDENCE], taken)],
        trades=items[:MAX_LISTED_TRADES],
        trades_listed=min(len(items), MAX_LISTED_TRADES),
        caveats=list(CAVEATS),
    )

