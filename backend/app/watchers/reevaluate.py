"""Turning a watcher event into an ordinary evaluation of its symbol.

The event does not decide anything. It only asks for the same full evaluation the
auto-scan would run: direction, levels and size come from the usual analysis and
risk code, and every normal gate (the confidence bar, position caps, the AI
overlay's objection, the market session) still applies.

The off-hours rule: a plan built while the market is closed would be priced off the
last session's close and could be filled at a stale price. So when the symbol's
market is shut nothing is evaluated here. A redo is queued instead (the same queue
that serves off-hours plans), and the redo job evaluates the symbol from fresh data
shortly after the next open. Crypto never closes, so it is evaluated at once.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, select

from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.llm_providers.base import LLMProvider
from app.markets import is_market_open_for
from app.portfolio.engine import Clock
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.services.deferred_evaluation_service import queue_market_open_redo
from app.services.trade_plan_service import generate_trade_plan
from app.watchers.base import WatcherEvent

logger = logging.getLogger(__name__)

SOURCE_PREFIX = "watcher:"


@dataclass
class ReevaluationOutcome:
    # "evaluated" | "queued_for_open" | "skipped_held" | "skipped_no_symbol" | "failed"
    status: str
    detail: str
    plan_id: int | None = None


def _trigger_text(event: WatcherEvent) -> str:
    return f"{event.watcher}: {event.headline}"


def reevaluate_for_event(
    session: Session,
    settings: AppSettings,
    event: WatcherEvent,
    now: datetime,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    *,
    clock: Clock | None = None,
) -> ReevaluationOutcome:
    """Evaluate `event.symbol` now if its market is open, else queue it for the
    next open. Never raises: a failure is returned as status "failed"."""
    symbol = event.symbol
    if not symbol:
        return ReevaluationOutcome("skipped_no_symbol", "A market-wide event names no symbol, so there is nothing to evaluate.")
    symbol = symbol.upper()

    open_symbols = {
        p.symbol for p in session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all()
    }
    if symbol in open_symbols:
        return ReevaluationOutcome("skipped_held", f"{symbol} is already held, so it was not re-evaluated.")

    trigger = _trigger_text(event)
    if not is_market_open_for(symbol, now):
        queue_market_open_redo(
            session,
            symbol,
            source=SOURCE_PREFIX + event.watcher,
            reason=event.headline,
            now=now,
            account_size=settings.paper_starting_cash,
            risk_pct=settings.default_risk_pct,
        )
        return ReevaluationOutcome(
            "queued_for_open", f"The market is closed, so {symbol} will be evaluated from fresh data after the next open."
        )

    # Same slot logic as the auto-scan: an evaluation is always made, but a plan
    # may only auto-execute while there is a free position slot.
    slots_remaining = max(0, settings.max_concurrent_positions - len(open_symbols))
    try:
        response = generate_trade_plan(
            symbol,
            settings.paper_starting_cash,
            settings.default_risk_pct,
            data_provider,
            llm_provider,
            session,
            allow_auto_execute=slots_remaining > 0,
            source=SOURCE_PREFIX + event.watcher,
            clock=clock,
            settings=settings,
        )
    except Exception as exc:
        logger.exception("Watcher re-evaluation of %s failed", symbol)
        session.rollback()
        return ReevaluationOutcome("failed", f"The evaluation of {symbol} failed: {exc}")

    if response.id is not None:
        record = session.get(TradePlanRecord, response.id)
        if record is not None:
            note = f"Triggered by {trigger}."
            record.auto_execute_note = f"{note} {record.auto_execute_note}".strip() if record.auto_execute_note else note
            session.add(record)
            session.commit()
    return ReevaluationOutcome("evaluated", f"{symbol} was evaluated ({response.status}).", plan_id=response.id)
