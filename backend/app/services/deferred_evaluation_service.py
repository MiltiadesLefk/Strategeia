"""The off-hours queue: evaluations that must wait for their market to open.

Decision D10 = C (plan.md): a plan made while the market is closed is built on
the last session's data — a Sunday plan on Friday's close — and by the open the
news and the opening price can make it stale. So it's never filled. It stays
pending, a redo is queued here, and shortly after the next open the redo job
(automation_service.run_market_open_redos) evaluates the symbol again from fresh
data; only that fresh plan may execute.

This module only reads and writes the queue. It deliberately doesn't import
trade_plan_service (which queues redos) or run anything, so both that service
and the redo job can depend on it without an import cycle.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.markets import is_always_on, is_market_open_for, next_us_open, to_market_time, us_session_bounds
from app.portfolio.models import DeferredEvaluation
from app.portfolio.sleeves import non_core_sleeve_id

# The redo runs this long after the open (09:45 ET on a normal day), not on
# the bell. The point of waiting was to decide on the new session's prices,
# and the first minutes are the opening auction and the providers' first
# prints of the day; a quarter of an hour later the quote and the day's first
# bar reflect trading, not the auction.
REDO_DELAY_AFTER_OPEN = timedelta(minutes=15)

# A redo that errors (every data source down, say) is retried on the job's
# next tick — every 15 minutes during the session — this many times in all
# before it's given up and the off-hours plan retired.
MAX_REDO_ATTEMPTS = 3

PENDING = "pending"


def _naive_utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def redo_due_at(now: datetime) -> datetime:
    """When an evaluation deferred at `now` will be redone: the next US open
    plus REDO_DELAY_AFTER_OPEN, as naive UTC."""
    return _naive_utc(next_us_open(now) + REDO_DELAY_AFTER_OPEN)


def redo_window_open(symbol: str, now: datetime) -> bool:
    """Whether a deferred evaluation of `symbol` may run at `now`: its market
    is open, and (for a US symbol) the session is at least
    REDO_DELAY_AFTER_OPEN old. Checked by the job itself, not just implied by
    due_at, so a redo whose due time passed while the app was down still
    waits for 09:45 of the session it finally runs in."""
    if not is_market_open_for(symbol, now):
        return False
    if is_always_on(symbol):
        return True
    bounds = us_session_bounds(to_market_time(now).date())
    return bounds is not None and to_market_time(now) >= bounds.open + REDO_DELAY_AFTER_OPEN


def queue_market_open_redo(
    session: Session,
    symbol: str,
    *,
    source: str,
    reason: str,
    now: datetime,
    trade_plan_id: int | None = None,
    account_size: float | None = None,
    risk_pct: float | None = None,
    sleeve_id: int | None = None,
) -> DeferredEvaluation:
    """Queue (or refresh) the redo for `symbol`. One pending row per symbol
    per sleeve (`sleeve_id`; None = core: two sleeves each waiting on the same
    symbol are two redos, each trading in its own account):
    a second off-hours request — Generate clicked twice on a Sunday, or the
    16:15 scan after a manual plan — points the existing row at the newest
    plan and sizing instead of queuing a duplicate evaluation. The first
    request's time and source are kept; the due time only ever moves later
    (a row left overdue while the app was down still waits for the next
    open, same as the new request would)."""
    due_at = redo_due_at(now)
    sleeve_id = non_core_sleeve_id(session, sleeve_id)
    existing = session.exec(
        select(DeferredEvaluation).where(
            DeferredEvaluation.symbol == symbol,
            DeferredEvaluation.status == PENDING,
            DeferredEvaluation.sleeve_id.is_(None) if sleeve_id is None else DeferredEvaluation.sleeve_id == sleeve_id,
        )
    ).first()
    if existing is not None:
        if trade_plan_id is not None:
            existing.trade_plan_id = trade_plan_id
        existing.account_size = account_size
        existing.risk_pct = risk_pct
        existing.reason = reason
        existing.due_at = max(existing.due_at, due_at)
        deferral = existing
    else:
        deferral = DeferredEvaluation(
            symbol=symbol,
            source=source,
            reason=reason,
            requested_at=now,
            due_at=due_at,
            trade_plan_id=trade_plan_id,
            account_size=account_size,
            risk_pct=risk_pct,
            sleeve_id=sleeve_id,
        )
    session.add(deferral)
    session.commit()
    session.refresh(deferral)
    return deferral


def due_redos(session: Session, now: datetime) -> list[DeferredEvaluation]:
    """Pending redos whose due time has passed, oldest first."""
    return list(
        session.exec(
            select(DeferredEvaluation)
            .where(DeferredEvaluation.status == PENDING, DeferredEvaluation.due_at <= now)
            .order_by(DeferredEvaluation.due_at, DeferredEvaluation.id)
        ).all()
    )


def pending_redo_for_plan(session: Session, plan_id: int | None) -> DeferredEvaluation | None:
    """The queued redo that will replace plan `plan_id`, if any. A plan with
    one can't be executed by hand: only the fresh plan the redo makes can."""
    if plan_id is None:
        return None
    return session.exec(
        select(DeferredEvaluation).where(
            DeferredEvaluation.trade_plan_id == plan_id, DeferredEvaluation.status == PENDING
        )
    ).first()


def pending_redo_times(session: Session) -> dict[int, datetime]:
    """plan id -> when its redo is due, for every plan awaiting one. One query
    for a whole list of plans instead of one per plan."""
    rows = session.exec(
        select(DeferredEvaluation).where(
            DeferredEvaluation.status == PENDING, DeferredEvaluation.trade_plan_id.is_not(None)
        )
    ).all()
    return {row.trade_plan_id: row.due_at for row in rows}
