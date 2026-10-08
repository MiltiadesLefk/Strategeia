from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session, select

from app.committee.gate import CommitteeGateBudget
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.universe import get_default_watchlist
from app.llm_providers.base import LLMProvider
from app.markets import format_market_time
from app.portfolio.engine import Clock, resolve_clock
from app.portfolio.models import DeferredEvaluation, PaperPosition, Sleeve, TradePlanRecord
from app.portfolio.sleeves import read_scope, scope_clause
from app.services.deferred_evaluation_service import MAX_REDO_ATTEMPTS, PENDING, due_redos, redo_window_open
from app.services.telegram_service import notify
from app.services.trade_plan_service import generate_trade_plan

logger = logging.getLogger(__name__)


@dataclass
class AutoScanOutcome:
    generated: list[str] = field(default_factory=list)
    no_trade: list[str] = field(default_factory=list)


def run_auto_scan(
    settings: AppSettings,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    session: Session,
) -> AutoScanOutcome:
    """Closes the full automation loop: every symbol in the bundled universe
    that doesn't already have an open position gets a *full* evaluation
    (price/volume/technicals/fundamentals/news/earnings, via
    generate_trade_plan) every run — quality over quantity, so the bot never
    silently skips a name, it either proposes a plan or explicitly logs why
    not (see TradePlanRecord.status == "no_trade").

    Guardrail: never AUTO-EXECUTES past max_concurrent_positions — without a
    cap, an unattended scan finding N setups every tick would open N new
    positions every tick with no limit. Evaluation itself is never capped
    though: once the cap is reached, remaining symbols still get a real plan
    (left "pending" for manual review) instead of not being looked at.

    Run while the market is closed (the 16:15 ET post-close scan, or the
    Auto-trade button at night), tradeable plans are not executed at all:
    generate_trade_plan queues each for a redo at the next open (D10 = C).
    """
    open_symbols = {p.symbol for p in session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all()}
    slots_remaining = max(0, settings.max_concurrent_positions - len(open_symbols))

    symbols = [s for s in get_default_watchlist(settings.scan_universe_size) if s not in open_symbols]

    outcome = AutoScanOutcome()
    gate_budget = CommitteeGateBudget(settings.committee_gate_max_per_scan)
    for symbol in symbols:
        try:
            response = generate_trade_plan(
                symbol,
                settings.paper_starting_cash,
                settings.default_risk_pct,
                data_provider,
                llm_provider,
                session,
                allow_auto_execute=slots_remaining > 0,
                source="auto_scan",
                committee_gate=gate_budget,
            )
        except Exception:
            logger.exception("Auto-scan: failed to evaluate %s", symbol)
            continue

        if response.direction is None or response.status == "no_trade":
            outcome.no_trade.append(symbol)
            continue

        outcome.generated.append(symbol)
        if response.status == "executed":
            slots_remaining -= 1

    if outcome.generated or outcome.no_trade:
        message = (
            f"Auto-scan evaluated {len(symbols)} symbol(s): {len(outcome.generated)} trade plan(s)"
            + (f" ({', '.join(outcome.generated)})" if outcome.generated else "")
            + f", {len(outcome.no_trade)} no-trade."
        )
        notify(settings.telegram_bot_token, settings.telegram_chat_id, message)
    return outcome


# ----------------------------------------------------------- market-open redo


@dataclass
class RedoOutcome:
    """What one pass of the market-open redo did, symbol by symbol."""

    executed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)  # a fresh plan, not auto-executed (see its note)
    no_trade: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    retrying: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def handled(self) -> int:
        return sum(len(group) for group in (self.executed, self.pending, self.no_trade, self.skipped, self.failed))


def _resolve(deferral: DeferredEvaluation, status: str, outcome: str, now: datetime) -> None:
    deferral.status = status
    deferral.outcome = outcome
    deferral.resolved_at = now


def _retire_off_hours_plan(session: Session, plan: TradePlanRecord | None, note: str) -> None:
    """Make sure the plan made while the market was closed can never be
    executed once its redo has run (D10 = C: only the fresh plan can). A
    tradeable fresh plan has already superseded it through
    generate_trade_plan's stale-pending rule; a no-trade result, a skip or a
    final failure doesn't, so it's discarded here. Either way its note stops
    promising a redo that has now happened."""
    if plan is None:
        return
    session.refresh(plan)
    if plan.status == "pending":
        plan.status = "discarded"
    plan.auto_execute_note = note
    session.add(plan)


def run_market_open_redos(
    settings: AppSettings,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    session: Session,
    *,
    clock: Clock | None = None,
) -> RedoOutcome:
    """D10 = C, the other half: every evaluation deferred because the market
    was closed is redone from fresh data once the market has been open for
    REDO_DELAY_AFTER_OPEN (09:45 ET on a normal day). The scheduler calls this
    every 15 minutes through the session, so a redo missed while the app was
    down, or one that failed, is picked up on a later tick.

    The fresh evaluation goes through generate_trade_plan like any other, so
    every normal check applies, auto-execute included — the
    auto_execute_trade_plans setting as it stands NOW decides whether the fresh
    plan opens a position, and the engine's own caps can still refuse it.
    """
    clock = resolve_clock(clock)
    now = clock()
    outcome = RedoOutcome()
    gate_budget = CommitteeGateBudget(settings.committee_gate_max_per_scan)

    for deferral in due_redos(session, now):
        symbol = deferral.symbol
        if not redo_window_open(symbol, now):
            continue  # a holiday, before 09:45, or after the close: wait for the next tick

        plan = session.get(TradePlanRecord, deferral.trade_plan_id) if deferral.trade_plan_id else None
        # The redo trades in the sleeve it was queued for (no sleeve = core), so
        # "already held" is asked of that sleeve's positions only.
        redo_sleeve = session.get(Sleeve, deferral.sleeve_id) if deferral.sleeve_id is not None else None
        held = session.exec(
            select(PaperPosition).where(
                PaperPosition.symbol == symbol,
                PaperPosition.status == "open",
                scope_clause(PaperPosition.sleeve_id, read_scope(session, redo_sleeve.key if redo_sleeve else None)),
            )
        ).first()
        if held is not None:
            _resolve(deferral, "skipped", f"{symbol} is already held (position #{held.id}), so there was nothing to redo.", now)
            _retire_off_hours_plan(
                session, plan, f"Market was closed when this plan was made. Not redone at the open: {symbol} is already held."
            )
            session.add(deferral)
            session.commit()
            outcome.skipped.append(symbol)
            continue
        if plan is not None and plan.status != "pending":
            # Executed or discarded since it was queued (superseded by a plan
            # made during the session, say): a fresh plan already exists.
            _resolve(deferral, "skipped", f"The off-hours plan #{plan.id} was already {plan.status}.", now)
            session.add(deferral)
            session.commit()
            outcome.skipped.append(symbol)
            continue

        # Resolved BEFORE the evaluation runs, so a redo that gets deferred
        # again (the bell rings mid-run) queues a new row rather than
        # refreshing this one, which is about to be marked done.
        deferral.attempts += 1
        _resolve(deferral, "done", "Being redone.", now)
        session.add(deferral)
        session.commit()

        try:
            response = generate_trade_plan(
                symbol,
                deferral.account_size or settings.paper_starting_cash,
                deferral.risk_pct or settings.default_risk_pct,
                data_provider,
                llm_provider,
                session,
                source="market_open_redo",
                clock=clock,
                sleeve=redo_sleeve,
                committee_gate=gate_budget,
            )
        except Exception as exc:
            logger.exception("Market-open redo failed for %s (attempt %d)", symbol, deferral.attempts)
            session.rollback()
            if deferral.attempts < MAX_REDO_ATTEMPTS:
                deferral.status = PENDING
                deferral.resolved_at = None
                deferral.outcome = f"Attempt {deferral.attempts} of {MAX_REDO_ATTEMPTS} failed ({exc}); retrying on the next tick."
                outcome.retrying.append(symbol)
            else:
                deferral.status = "failed"
                deferral.outcome = f"Couldn't be redone after {deferral.attempts} attempts: {exc}"
                _retire_off_hours_plan(
                    session,
                    plan,
                    "Market was closed when this plan was made, and the redo at the open failed "
                    f"({exc}). Generate a fresh plan by hand.",
                )
                outcome.failed.append(symbol)
            session.add(deferral)
            session.commit()
            continue

        deferral.result_plan_id = response.id
        when = format_market_time(now)
        if response.direction is None or response.status == "no_trade":
            deferral.outcome = f"Redone at {when}: no trade. {response.reason or ''}".strip()
            outcome.no_trade.append(symbol)
        elif response.status == "executed":
            deferral.outcome = f"Redone at {when}: fresh plan #{response.id} executed as a paper position."
            outcome.executed.append(symbol)
        else:
            deferral.outcome = (
                f"Redone at {when}: fresh plan #{response.id} is pending"
                + (f" ({response.auto_execute_note})" if response.auto_execute_note else " (auto-execute is off).")
            )
            outcome.pending.append(symbol)
        _retire_off_hours_plan(
            session,
            plan,
            f"Market was closed when this plan was made. Redone from fresh data at {when}: see plan #{response.id}.",
        )
        session.add(deferral)
        session.commit()

    if outcome.handled:
        parts = [
            f"{label}: {', '.join(symbols)}"
            for label, symbols in (
                ("executed", outcome.executed),
                ("fresh plan pending", outcome.pending),
                ("no trade", outcome.no_trade),
                ("skipped", outcome.skipped),
                ("failed", outcome.failed),
            )
            if symbols
        ]
        notify(settings.telegram_bot_token, settings.telegram_chat_id, "Market-open redo — " + "; ".join(parts) + ".")
    return outcome
