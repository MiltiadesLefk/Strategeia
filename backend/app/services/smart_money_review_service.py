"""The smart-money review queue (NEW_PLAN section 6 step 7, section 7): list, Buy, Skip.

A candidate lands here when the full evaluation rated it badly (or rated it tradeable but the auto sleeve
did not take it). The person decides:

* Buy opens the position in the `smart-money-manual` sleeve through `engine.open_position`, from a fresh
  evaluation on today's data (see `smart_money_pipeline_service.execute_review_buy`). The market closed
  means the Buy is queued as a `DeferredEvaluation` and filled at the open from fresh data, never from a
  stale price. A refused Buy (a cap, cash, a duplicate, no long trade today) is a 409 with the reason.
* Skip records the decision and an optional note.

Both store who decided and when. Nothing here sets a direction, size, stop or target. GETs never write.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.markets import is_market_open_for, next_us_open, format_market_time
from app.portfolio.engine import Clock, resolve_clock
from app.portfolio.models import PaperPosition
from app.portfolio.sleeves import CORE_SLEEVE_KEY, read_scope, scope_clause
from app.portfolio.missed_trade_models import MissedTradeOutcome
from app.portfolio.missed_trade_report import (
    MIN_TRADES_FOR_READING,
    STATE_AWAITING,
    STATE_NOT_SIMULATED,
    STATE_OPEN,
    STATE_RESOLVED,
    _r_stats,
    _state_of,
    _win_rate,
)
from app.portfolio.missed_trades import classify_plan
from app.portfolio.models import TradePlanRecord
from app.schemas.smart_money_review_schemas import (
    HindsightGroupOut,
    HindsightOut,
    ReviewBuyResponse,
    ReviewComponentOut,
    ReviewItemOut,
    ReviewListResponse,
    ReviewPlanOut,
    ReviewRatingOut,
)
from app.services.smart_money_pipeline_service import (
    ReviewBuyMarketClosed,
    ReviewBuyRefused,
    _defer_candidate,
    cancel_queued_deferrals,
    execute_review_buy,
)
from app.services.sleeve_service import ensure_smart_money_sleeves
from app.smartmoney.models import (
    STATUS_AUTO_BOUGHT,
    STATUS_BOUGHT,
    STATUS_EVALUATING,
    STATUS_EXPIRED,
    STATUS_NEEDS_REVIEW,
    STATUS_NEW,
    STATUS_SKIPPED,
    SmartMoneyCandidate,
)
from app.smartmoney.pipeline_rules import SOURCE_REVIEW_BUY
from app.smartmoney.tracker import SOURCE_DELAY_NOTES

REVIEW_STATUSES = (STATUS_NEEDS_REVIEW, STATUS_BOUGHT, STATUS_SKIPPED, STATUS_EXPIRED, STATUS_AUTO_BOUGHT)
# ?status=decided: everything that is not waiting for a person.
DECIDED = "decided"
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

DISCLAIMER = (
    "These stocks were flagged because of REPORTED filings (Form 4: up to 2 business days late; House: up to 45 days; "
    "13F: as of a quarter end, up to 45 days late), not because anyone is known to know anything. The score is a starting "
    "guess that has not been validated, and the rating beside it is the app's own full evaluation. Buying from this "
    "queue is a paper trade in its own account, kept apart so the overrides can be measured against the rating."
)


class ReviewNotFound(Exception):
    pass


class ReviewConflict(Exception):
    """The item cannot be acted on (wrong state, a refused Buy). The message is shown to the person (HTTP 409)."""


# --------------------------------------------------------------------------
# read side
# --------------------------------------------------------------------------


def _delay_note(row: SmartMoneyCandidate) -> str:
    sources = sorted({str(c.get("source", "")) for c in (row.components or [])})
    notes = " ".join(SOURCE_DELAY_NOTES[s] for s in sources if s in SOURCE_DELAY_NOTES)
    head = f"Reported filings, counted as of {row.scored_at:%Y-%m-%d}; the trades themselves happened earlier."
    queued = (
        " Your Buy is queued: it is filled from fresh data once the market is open and the session is 15 minutes old."
        if row.buy_queued_at is not None
        else ""
    )
    return f"{head} {notes}{queued}".strip()


def _rating(plan: TradePlanRecord | None) -> ReviewRatingOut | None:
    if plan is None:
        return None
    if plan.status == "no_trade":
        text = f"No trade: {plan.reason or 'the evaluation declined it.'}"
    elif plan.status == "executed":
        text = f"Tradeable {plan.direction or ''} plan; bought."
    else:
        note = (plan.auto_execute_note or "").strip()
        text = f"Tradeable {plan.direction or ''} plan, not bought" + (f": {note}" if note else ".")
    return ReviewRatingOut(
        confidence_score=plan.confidence_score,
        confidence_points=plan.confidence_points,
        confidence_points_max=plan.confidence_points_max,
        verdict_text=text,
    )


def _reasons_declined(row: SmartMoneyCandidate, plan: TradePlanRecord | None) -> list[str]:
    out: list[str] = []
    if plan is not None and plan.status == "no_trade" and plan.reason:
        out.append(plan.reason)
    if plan is not None and plan.auto_execute_note:
        out += [line.strip() for line in plan.auto_execute_note.splitlines() if line.strip()]
    if row.reason_detail:  # the plain-English detail only; the machine code (`expired`, `skipped_by_user`) is not a reason to show
        out.append(row.reason_detail)
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def _plan_out(plan: TradePlanRecord | None) -> ReviewPlanOut | None:
    if plan is None:
        return None
    return ReviewPlanOut(
        status=plan.status,
        direction=plan.direction,
        entry=plan.entry,
        stop=plan.stop,
        suggested_shares=plan.suggested_shares,
        auto_execute_note=plan.auto_execute_note,
        created_at=plan.created_at,
    )


def held_in_core(session: Session, symbol: str) -> bool:
    """True when the core sleeve has this symbol open: the same stock can sit in core and in a
    smart-money sleeve (the duplicate rule is per sleeve), so the person is told, not surprised."""
    scope = read_scope(session, CORE_SLEEVE_KEY)
    return (
        session.exec(
            select(PaperPosition.id).where(
                PaperPosition.symbol == symbol, PaperPosition.status == "open", scope_clause(PaperPosition.sleeve_id, scope)
            )
        ).first()
        is not None
    )


def item_out(session: Session, row: SmartMoneyCandidate, *, now: datetime | None = None) -> ReviewItemOut:
    plan = session.get(TradePlanRecord, row.plan_id) if row.plan_id else None
    now = now or resolve_clock(None)()  # the engine's notion of now, so market_open agrees with what a Buy would do
    return ReviewItemOut(
        id=row.id or 0,
        symbol=row.symbol,
        status=row.status,
        score=row.score,
        components=[
            ReviewComponentOut(
                source=str(c.get("source", "")),
                points=float(c.get("points", 0.0)),
                reason=str(c.get("reason", "")),
                delay_note=SOURCE_DELAY_NOTES.get(str(c.get("source", ""))),
            )
            for c in (row.components or [])
        ],
        plan_id=row.plan_id,
        rating=_rating(plan),
        reasons_declined=_reasons_declined(row, plan),
        expires_at=row.expires_at,
        created_at=row.created_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        note=row.notes,
        delay_note=_delay_note(row),
        market_open=is_market_open_for(row.symbol, now),
        plan=_plan_out(plan),
        sleeve_key=row.sleeve_key,
        position_id=row.position_id,
        reason=row.reason,
        buy_queued=row.buy_queued_at is not None,
        scored_at=row.scored_at,
        also_held_in_core=held_in_core(session, row.symbol),
    )


def hindsight(session: Session) -> HindsightOut:
    """What the items nobody bought would have earned, from the missed-trades ledger's own stored results
    (never recomputed here, and a GET computes nothing): the replay lives in portfolio/missed_trades.py.
    Items the person skipped, items that expired, and the ones still waiting are kept apart."""
    rows = session.exec(
        select(SmartMoneyCandidate).where(
            SmartMoneyCandidate.plan_id.is_not(None),
            SmartMoneyCandidate.status.in_((STATUS_SKIPPED, STATUS_EXPIRED, STATUS_NEEDS_REVIEW)),
        )
    ).all()
    outcomes = {o.plan_id: o for o in session.exec(select(MissedTradeOutcome)).all()}
    buckets: dict[str, list[tuple[str, MissedTradeOutcome | None]]] = {"user_skipped": [], "expired": [], "undecided": []}
    for row in rows:
        plan = session.get(TradePlanRecord, row.plan_id)
        if plan is None or plan.status not in ("pending", "no_trade"):
            continue
        classification = classify_plan(plan)
        if classification is None:
            continue
        # Skipped by the pipeline itself (illiquid, error) never has a plan, so a skipped row with a plan is a person's.
        key = {STATUS_SKIPPED: "user_skipped", STATUS_EXPIRED: "expired"}.get(row.status, "undecided")
        buckets[key].append((_state_of(classification, outcomes.get(plan.id)), outcomes.get(plan.id)))
    labels = {
        "user_skipped": "Skipped by you",
        "expired": "Expired undecided",
        "undecided": "Still waiting for a decision",
    }
    groups: list[HindsightGroupOut] = []
    for key, entries in buckets.items():
        resolved = [o.r_multiple for state, o in entries if state == STATE_RESOLVED and o is not None and o.r_multiple is not None]
        wins, avg, low, high, _total = _r_stats(resolved)
        rate, _, _ = _win_rate(wins, len(resolved))
        groups.append(
            HindsightGroupOut(
                key=key,
                label=labels[key],
                n=len(entries),
                resolved=len(resolved),
                open=sum(1 for state, _ in entries if state == STATE_OPEN),
                awaiting=sum(1 for state, _ in entries if state == STATE_AWAITING),
                not_simulated=sum(1 for state, _ in entries if state == STATE_NOT_SIMULATED),
                wins=wins,
                win_rate=rate,
                avg_r=avg,
                avg_r_low=low,
                avg_r_high=high,
                small_sample=len(resolved) < MIN_TRADES_FOR_READING,
            )
        )
    return HindsightOut(
        groups=groups,
        min_trades_for_reading=MIN_TRADES_FOR_READING,
        note=(
            "Hypothetical trades from the missed-trades ledger (daily bars, filled at the last known close). Below "
            f"{MIN_TRADES_FOR_READING} resolved trades a group says nothing."
        ),
    )


def list_review(
    session: Session, *, status: str | None = None, limit: int = DEFAULT_LIMIT, now: datetime | None = None
) -> ReviewListResponse:
    """Items of the queue, newest first. No status = what waits for a decision (needs_review); `decided` = the
    rest; or one status by name. Read-only."""
    query = select(SmartMoneyCandidate)
    if status is None or status == STATUS_NEEDS_REVIEW:
        query = query.where(SmartMoneyCandidate.status == STATUS_NEEDS_REVIEW)
    elif status == DECIDED:
        query = query.where(SmartMoneyCandidate.status.in_((STATUS_BOUGHT, STATUS_SKIPPED, STATUS_EXPIRED, STATUS_AUTO_BOUGHT)))
    elif status in REVIEW_STATUSES or status in (STATUS_NEW, STATUS_EVALUATING):
        query = query.where(SmartMoneyCandidate.status == status)
    rows = session.exec(query.order_by(SmartMoneyCandidate.created_at.desc(), SmartMoneyCandidate.id.desc())).all()
    shown = rows[: max(1, min(limit, MAX_LIMIT))]
    return ReviewListResponse(
        items=[item_out(session, r, now=now) for r in shown], disclaimer=DISCLAIMER, hindsight=hindsight(session)
    )


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------


def _get_waiting(session: Session, item_id: int) -> SmartMoneyCandidate:
    row = session.get(SmartMoneyCandidate, item_id)
    if row is None:
        raise ReviewNotFound(f"No smart-money review item {item_id}.")
    if row.status != STATUS_NEEDS_REVIEW:
        raise ReviewConflict(f"{row.symbol} is already {row.status}; only items waiting for review can be decided.")
    return row


def buy_review_item(
    session: Session,
    settings: AppSettings,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    item_id: int,
    *,
    clock: Clock | None = None,
) -> ReviewBuyResponse:
    """Buy from the queue. Raises ReviewNotFound (404) or ReviewConflict (409, message for the person)."""
    clock = resolve_clock(clock)
    row = _get_waiting(session, item_id)
    if row.buy_queued_at is not None:
        raise ReviewConflict(f"A buy of {row.symbol} is already queued for the open.")
    try:
        execute_review_buy(session, row, settings, data_provider, llm_provider, clock=clock)
    except ReviewBuyMarketClosed:
        _queue_buy(session, settings, row, clock())
    except ReviewBuyRefused as exc:
        raise ReviewConflict(str(exc)) from exc
    session.refresh(row)
    return ReviewBuyResponse(**item_out(session, row, now=clock()).model_dump())


def _queue_buy(session: Session, settings: AppSettings, row: SmartMoneyCandidate, now: datetime) -> None:
    """The market is closed: nothing is filled at last session's price. A redo at the open does the Buy."""
    _auto, manual = ensure_smart_money_sleeves(session)
    _defer_candidate(session, row, manual, settings, now, source=SOURCE_REVIEW_BUY)
    row.buy_queued_at = now
    row.reason_detail = (
        f"Buy queued: the market is closed. It will be bought from fresh data at the next open "
        f"({format_market_time(next_us_open(now))}), if it still passes the checks then."
    )
    session.add(row)
    session.commit()


def skip_review_item(session: Session, item_id: int, note: str | None = None, *, clock: Clock | None = None) -> ReviewItemOut:
    clock = resolve_clock(clock)
    now = clock()
    row = _get_waiting(session, item_id)
    row.status = STATUS_SKIPPED
    row.reason = row.reason or "skipped_by_user"
    row.decided_by = "user"
    row.decided_at = now
    row.notes = (note or "").strip() or None
    row.buy_queued_at = None
    cancel_queued_deferrals(session, row.id, "You skipped the item before the queued buy ran.", now)
    session.add(row)
    session.commit()
    session.refresh(row)
    return item_out(session, row, now=now)


def setup_sleeves(session: Session) -> tuple[str, str, bool]:
    """Explicit creation of the two smart-money sleeves. Returns (auto key, manual key, whether any was new)."""
    from app.portfolio.models import Sleeve

    had = {s.key for s in session.exec(select(Sleeve)).all()}
    auto, manual = ensure_smart_money_sleeves(session)
    return auto.key, manual.key, not ({auto.key, manual.key} <= had)


__all__ = [
    "ReviewConflict",
    "ReviewNotFound",
    "buy_review_item",
    "list_review",
    "skip_review_item",
    "setup_sleeves",
]
