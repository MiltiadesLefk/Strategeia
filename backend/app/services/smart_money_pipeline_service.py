"""From a smart-money candidate to a decision (NEW_PLAN section 6 steps 4-8, section 7).

The tracker (`watchers/smart_money_watcher.py`) only records `new` candidates. This service takes each
one through the SAME evaluation every other symbol gets and routes the outcome:

    new -> liquidity floor -> (market closed? wait for the open) -> the hold scan step
        tradeable, bought automatically ........ auto_bought   (sleeve smart-money-auto)
        rated bad, or tradeable but not bought .. needs_review  (the review queue: the person decides)
        illiquid / no prices / error ............ skipped, with the reason

Boundaries that matter (CLAUDE.md, NEW_PLAN section 2):

* Nothing here sets a direction, entry, stop, target or size. They come from the hold buy
  (`hold_buy_service`) exactly as for the daily scan, with the quality gate, the engine's session, cash,
  duplicate, position-cap and sector checks, and `auto_execute_trade_plans`. The score only decides WHICH symbol
  gets looked at; the candidate id, score and components are recorded on the plan for later reports,
  never read by a decision.
* Long only (spot): the evaluation runs with `long_only=True`, so a bearish trend is a no-trade
  instead of a short.
* A market that is closed means nothing is evaluated or filled from a stale price: the candidate waits
  in `DeferredEvaluation` (carrying its id) and `automation_service.run_market_open_redos` hands it back
  to `handle_redo` at 09:45 ET, which evaluates from fresh data.
* Caps keep "no trade" days normal: `smart_money_max_open_positions` and `smart_money_max_new_per_week`
  per sleeve. A tradeable plan the auto sleeve may not take (cap reached, auto-execute off) is not
  dropped: it goes to the review queue with the reason.

Every function here that writes takes the session it is given; the scheduler job opens one.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.config import AppSettings
from app.data_providers.base import AllProvidersFailedError, DataProvider, DataProviderError
from app.data_providers.cache import fresh_data_only
from app.llm_providers.base import LLMProvider
from app.markets import format_market_time, is_market_open_for
from app.portfolio.engine import (
    Clock,
    DuplicatePositionError,
    InsufficientCashError,
    MarketClosedError,
    MaxPositionsExceededError,
    SectorConcentrationError,
    SleeveDisabledError,
    StalePlanError,
    resolve_clock,
)
from app.portfolio.models import DeferredEvaluation, PaperPosition, Sleeve, TradePlanRecord
from app.services.deferred_evaluation_service import MAX_REDO_ATTEMPTS, PENDING, queue_market_open_redo
from app.services.sleeve_service import build_sleeve_engine, ensure_smart_money_sleeves
from app.services.telegram_service import notify
from app.smartmoney.liquidity import REASON_NO_PRICES, check_liquidity
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
from app.smartmoney.pipeline_rules import NEW_POSITION_WINDOW_DAYS, SOURCE_AUTO, SOURCE_REVIEW_BUY
from app.smartmoney.scoring import REVIEW_EXPIRY_DAYS
from app.smartmoney.tracker import SOURCE_DELAY_NOTES

logger = logging.getLogger(__name__)

REASON_EVALUATION_ERROR = "evaluation_error"
REASON_REDO_FAILED = "redo_failed"

# The engine refusals that mean "not this time", not "something broke".
_ENGINE_REFUSALS = (
    InsufficientCashError,
    DuplicatePositionError,
    MaxPositionsExceededError,
    SectorConcentrationError,
    SleeveDisabledError,
    StalePlanError,
)


class ReviewBuyRefused(Exception):
    """A review-queue Buy that cannot happen right now; the message says why (shown to the person, HTTP 409)."""


class ReviewBuyMarketClosed(Exception):
    """The market closed between the caller's check and the fill: queue the Buy for the open instead."""


@dataclass
class PipelineOutcome:
    auto_bought: list[str] = field(default_factory=list)
    needs_review: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    waiting_for_open: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _naive(now: datetime) -> datetime:
    return now.replace(tzinfo=None) if now.tzinfo is not None else now


def sleeve_cap_note(session: Session, settings: AppSettings, sleeve: Sleeve, now: datetime) -> str | None:
    """Why `sleeve` may not take another position right now (open-position cap, new-per-week cap), or None.
    Counts only this sleeve's rows: the caps are per sleeve, like the engine's own."""
    open_n = len(
        session.exec(
            select(PaperPosition.id).where(PaperPosition.sleeve_id == sleeve.id, PaperPosition.status == "open")
        ).all()
    )
    if open_n >= settings.smart_money_max_open_positions:
        return (
            f"The '{sleeve.name}' sleeve already holds {open_n} positions, its cap "
            f"(smart_money_max_open_positions = {settings.smart_money_max_open_positions})."
        )
    since = _naive(now) - timedelta(days=NEW_POSITION_WINDOW_DAYS)
    week_n = len(
        session.exec(
            select(PaperPosition.id).where(PaperPosition.sleeve_id == sleeve.id, PaperPosition.opened_at >= since)
        ).all()
    )
    if week_n >= settings.smart_money_max_new_per_week:
        return (
            f"The '{sleeve.name}' sleeve already opened {week_n} position(s) in the last {NEW_POSITION_WINDOW_DAYS} days, "
            f"its cap (smart_money_max_new_per_week = {settings.smart_money_max_new_per_week})."
        )
    return None


def alert_text_components(candidate: SmartMoneyCandidate, limit: int = 5) -> str:
    lines = [f"- {c.get('reason', '')} ({float(c.get('points', 0)):+g})" for c in (candidate.components or [])[:limit]]
    sources = sorted({str(c.get("source", "")) for c in (candidate.components or [])})
    delays = [SOURCE_DELAY_NOTES[s] for s in sources if s in SOURCE_DELAY_NOTES]
    return "\n".join(lines + [f"  {d}" for d in delays])


def _components_json(candidate: SmartMoneyCandidate) -> str:
    return json.dumps(candidate.components or [])


def _stamp_plan(session: Session, plan_id: int | None, candidate: SmartMoneyCandidate, source: str) -> TradePlanRecord | None:
    """Record on the plan which candidate, score and components led to it. Descriptive only."""
    if plan_id is None:
        return None
    record = session.get(TradePlanRecord, plan_id)
    if record is None:
        return None
    record.source = source
    record.smart_money_candidate_id = candidate.id
    record.smart_money_score = candidate.score
    record.smart_money_components = _components_json(candidate)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def _skip(session: Session, candidate: SmartMoneyCandidate, reason: str, detail: str, now: datetime) -> str:
    candidate.status = STATUS_SKIPPED
    candidate.reason = reason
    candidate.reason_detail = detail[:500]
    candidate.decided_by = "auto"
    candidate.decided_at = now
    session.add(candidate)
    session.commit()
    logger.info("Smart-money candidate %s (%s) skipped: %s - %s", candidate.id, candidate.symbol, reason, detail)
    return STATUS_SKIPPED


def _plan_line(plan: TradePlanRecord | None) -> str:
    if plan is None:
        return "no plan"
    if plan.status == "no_trade":
        return f"no trade: {plan.reason}"
    return f"{(plan.direction or '').upper()} plan, confidence {plan.confidence_score}%"


CORE_NOTE = "\nAlso held in the core sleeve (a separate paper account)."


def _core_note(session: Session | None, symbol: str) -> str:
    if session is None:
        return ""
    from app.services.smart_money_review_service import held_in_core

    return CORE_NOTE if held_in_core(session, symbol) else ""


def _alert_review(
    settings: AppSettings, candidate: SmartMoneyCandidate, plan: TradePlanRecord | None, why: str, session: Session | None = None
) -> None:
    text = (
        f"Smart-money review: {candidate.symbol} is waiting for your decision.\n"
        f"Reported filings (not a prediction; reports can be days to weeks late), as of {candidate.scored_at:%Y-%m-%d}: "
        f"score {candidate.score:g}\n{alert_text_components(candidate)}\n"
        f"The full evaluation said: {_plan_line(plan)}. {why}\n"
        f"Buy or skip it on the Smart Money page; it expires {candidate.expires_at:%Y-%m-%d}."
        f"{_core_note(session, candidate.symbol)}"
    )
    notify(settings.telegram_bot_token, settings.telegram_chat_id, text)


def _alert_auto_buy(
    settings: AppSettings, candidate: SmartMoneyCandidate, plan: TradePlanRecord | None, session: Session | None = None
) -> None:
    text = (
        f"Smart-money auto-buy (paper): {candidate.symbol} was bought in the '{candidate.sleeve_key}' sleeve after a full "
        f"evaluation ({_plan_line(plan)}).\n"
        f"Why it was looked at (reported filings, as of {candidate.scored_at:%Y-%m-%d}; not a prediction): score {candidate.score:g}\n"
        f"{alert_text_components(candidate)}"
        f"{_core_note(session, candidate.symbol)}"
    )
    notify(settings.telegram_bot_token, settings.telegram_chat_id, text)


# --------------------------------------------------------------------------
# evaluation and routing
# --------------------------------------------------------------------------


def _liquidity(candidate: SmartMoneyCandidate, data_provider: DataProvider, now: datetime):
    try:
        with fresh_data_only():
            bars = data_provider.get_ohlcv(candidate.symbol, period="3mo", interval="1d")
    except (AllProvidersFailedError, DataProviderError):
        bars = None
    return check_liquidity(candidate.symbol, bars, now)


def _defer_candidate(
    session: Session, candidate: SmartMoneyCandidate, sleeve: Sleeve, settings: AppSettings, now: datetime,
    *, source: str, plan_id: int | None = None,
) -> None:
    queue_market_open_redo(
        session,
        candidate.symbol,
        source=source,
        reason=f"The market is closed; the smart-money evaluation of {candidate.symbol} waits for the open.",
        now=now,
        trade_plan_id=plan_id,
        account_size=sleeve.starting_cash,
        risk_pct=settings.default_risk_pct,
        sleeve_id=sleeve.id,
        candidate_id=candidate.id,
    )


def evaluate_candidate(
    session: Session,
    candidate: SmartMoneyCandidate,
    settings: AppSettings,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    *,
    clock: Clock | None = None,
    after_open_wait: bool = False,
) -> str:
    """Run one candidate through the pipeline and return its new status ("evaluating" = waiting for the open).
    `after_open_wait` is set by the market-open redo: the market is open, so the evaluation proceeds."""
    clock = resolve_clock(clock)
    now = _naive(clock())
    if candidate.status not in (STATUS_NEW, STATUS_EVALUATING):
        return candidate.status
    candidate.status = STATUS_EVALUATING
    session.add(candidate)
    session.commit()

    verdict = _liquidity(candidate, data_provider, now)
    if not verdict.ok:
        return _skip(session, candidate, verdict.reason or REASON_NO_PRICES, verdict.detail, now)

    auto, _manual = ensure_smart_money_sleeves(session)
    if not after_open_wait and not is_market_open_for(candidate.symbol, now):
        _defer_candidate(session, candidate, auto, settings, now, source=SOURCE_AUTO)
        candidate.reason = "waiting_for_open"
        candidate.reason_detail = "The market is closed; the evaluation runs from fresh data at the next open."
        session.add(candidate)
        session.commit()
        return STATUS_EVALUATING

    cap_note = sleeve_cap_note(session, settings, auto, now)
    return _evaluate_hold(session, candidate, settings, data_provider, auto, cap_note, clock, now)


def _evaluate_hold(
    session: Session, candidate: SmartMoneyCandidate, settings: AppSettings, data_provider: DataProvider,
    sleeve: Sleeve, cap_note: str | None, clock: Clock, now: datetime,
) -> str:
    """Hold mode (PORTOFOLIO_BUILDER step 3): the quality gate decides instead of a swing plan. A pass inside the
    sleeve caps is hold-bought at the target weight; a failed gate, a reached cap or an engine refusal goes to the
    review queue with the reason. A failed gate does not stop a smart-money buy: it buys a smaller slice
    (`hold_failed_gate_size_pct`). The smart-money score only chose which stock was looked at."""
    from app.services.hold_buy_service import check_quality, hold_buy

    try:
        gate = check_quality(data_provider, candidate.symbol, session=session)
    except Exception as exc:  # one bad symbol must not stop the others
        logger.exception("Smart-money quality check of %s failed", candidate.symbol)
        session.rollback()
        candidate = session.get(SmartMoneyCandidate, candidate.id)
        return _skip(session, candidate, REASON_EVALUATION_ERROR, f"{type(exc).__name__}: {exc}", now)
    from app.services.market_find_service import LANE_SMART_MONEY, adopt_found_stock

    # Whole-market: a found stock is analysed, alerted and joins the universe even when it fails the gate or is not bought.
    adopt_found_stock(session, settings, data_provider, candidate.symbol, LANE_SMART_MONEY, f"smart money score {candidate.score:g}", gate)
    if cap_note is not None:
        return route_plan(session, candidate, None, "pending", False, cap_note, settings, sleeve, now)
    if not settings.auto_execute_trade_plans:
        return route_plan(session, candidate, None, "pending", False, None, settings, sleeve, now,
                          note="Auto-execute is off, so nothing is bought on its own.")
    engine = build_sleeve_engine(session, data_provider, settings, sleeve, clock=clock, hold=True)
    try:
        position = hold_buy(session, engine, data_provider, settings, candidate.symbol, sleeve_id=sleeve.id,
                            reason=f"[smart money lane] score {candidate.score:g}; "
                            + ("passed the quality gate" if gate.passed else "failed the quality gate (" + "; ".join(gate.failed_reasons) + "), half size"),
                            size_pct=100.0 if gate.passed else settings.hold_failed_gate_size_pct)
    except (MarketClosedError, *_ENGINE_REFUSALS) as exc:
        return route_plan(session, candidate, None, "pending", False, None, settings, sleeve, now, note=str(exc))
    return route_plan(session, candidate, position.trade_plan_id, "executed", False, None, settings, sleeve, now)


def route_plan(
    session: Session,
    candidate: SmartMoneyCandidate,
    plan_id: int | None,
    status: str | None,
    deferred: bool,
    cap_note: str | None,
    settings: AppSettings,
    sleeve: Sleeve,
    now: datetime,
    note: str | None = None,
) -> str:
    """Decide where an evaluated candidate goes. Only reads what the evaluation already decided."""
    candidate = session.get(SmartMoneyCandidate, candidate.id)
    plan = _stamp_plan(session, plan_id, candidate, SOURCE_AUTO)
    candidate.plan_id = plan_id
    candidate.sleeve_key = sleeve.key

    if deferred:
        # The market closed during the evaluation: the plan describes the last session, so it waits for the open.
        pending = session.exec(
            select(DeferredEvaluation).where(
                DeferredEvaluation.symbol == candidate.symbol,
                DeferredEvaluation.status == PENDING,
                DeferredEvaluation.sleeve_id == sleeve.id,
            )
        ).first()
        if pending is not None:
            pending.candidate_id = candidate.id
            session.add(pending)
        candidate.status = STATUS_EVALUATING
        candidate.reason = "waiting_for_open"
        session.add(candidate)
        session.commit()
        return STATUS_EVALUATING

    if status == "executed":
        position = session.exec(select(PaperPosition).where(PaperPosition.trade_plan_id == plan_id)).first()
        candidate.status = STATUS_AUTO_BOUGHT
        candidate.decided_by = "auto"
        candidate.decided_at = now
        candidate.position_id = position.id if position is not None else None
        candidate.reason = None
        session.add(candidate)
        session.commit()
        _alert_auto_buy(settings, candidate, plan, session)
        return STATUS_AUTO_BOUGHT

    # A bad rating (no trade), or a tradeable plan the auto sleeve did not take: the person decides.
    candidate.status = STATUS_NEEDS_REVIEW
    candidate.expires_at = now + timedelta(days=REVIEW_EXPIRY_DAYS)
    if status == "pending":
        candidate.reason = "tradeable_not_bought"
        candidate.reason_detail = (note or cap_note or (plan.auto_execute_note if plan is not None else None) or "Auto-buy did not open it.")[:500]
    else:
        candidate.reason = "rated_bad"
        candidate.reason_detail = (note or (plan.reason if plan is not None else None) or "The evaluation found no trade.")[:500]
    session.add(candidate)
    session.commit()
    _alert_review(settings, candidate, plan, candidate.reason_detail or "", session)
    return STATUS_NEEDS_REVIEW


def run_pipeline(
    session: Session,
    settings: AppSettings,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    *,
    clock: Clock | None = None,
    max_candidates: int | None = None,
) -> PipelineOutcome:
    """Evaluate every `new` candidate (oldest first, at most `max_candidates` per run). Called by the scheduler."""
    clock = resolve_clock(clock)
    outcome = PipelineOutcome()
    max_candidates = max_candidates or settings.smart_money_max_evaluations_per_run
    rows = session.exec(
        select(SmartMoneyCandidate)
        .where(SmartMoneyCandidate.status == STATUS_NEW)
        .order_by(SmartMoneyCandidate.created_at, SmartMoneyCandidate.id)
    ).all()[:max_candidates]
    for row in rows:
        symbol = row.symbol
        try:
            status = evaluate_candidate(session, row, settings, data_provider, llm_provider, clock=clock)
        except Exception:
            logger.exception("Smart-money pipeline failed on %s", symbol)
            session.rollback()
            continue
        {
            STATUS_AUTO_BOUGHT: outcome.auto_bought,
            STATUS_NEEDS_REVIEW: outcome.needs_review,
            STATUS_SKIPPED: outcome.skipped,
            STATUS_EVALUATING: outcome.waiting_for_open,
        }.get(status, []).append(symbol)
    return outcome


# --------------------------------------------------------------------------
# the review-queue Buy
# --------------------------------------------------------------------------


def execute_review_buy(
    session: Session,
    candidate: SmartMoneyCandidate,
    settings: AppSettings,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    *,
    clock: Clock | None = None,
) -> PaperPosition:
    """Open the position a person chose to buy, in the smart-money-manual sleeve, through the engine.

    A fresh evaluation is made on today's data (so the entry, stop and size are the app's own and the
    price is not a stale one), with ONLY the confidence bar lowered: the person has read the rating and
    bought anyway. Everything else still applies: a neutral or bearish trend, an AI-overlay or ML
    objection, the session gate, cash, duplicate, position and sector caps, and the sleeve caps. Raises
    ReviewBuyRefused (message for the person) or ReviewBuyMarketClosed (queue it)."""
    clock = resolve_clock(clock)
    now = _naive(clock())
    _auto, manual = ensure_smart_money_sleeves(session)
    note = sleeve_cap_note(session, settings, manual, now)
    if note:
        raise ReviewBuyRefused(note)
    engine = build_sleeve_engine(session, data_provider, settings, manual, clock=clock)
    if not engine.is_market_open_for(candidate.symbol):
        raise ReviewBuyMarketClosed(candidate.symbol)

    old_plan_id = candidate.plan_id
    record, position = _review_buy_hold(session, candidate, settings, data_provider, manual, clock)
    return _finish_review_buy(session, candidate, manual, record, position, old_plan_id, clock)


def _review_buy_hold(session, candidate, settings, data_provider, manual, clock):
    """The review Buy in hold mode: the person has read why it failed the gate, so the gate is skipped; every
    engine check still applies (session, duplicate, caps, stale quote, cash)."""
    from app.services.hold_buy_service import hold_buy

    engine = build_sleeve_engine(session, data_provider, settings, manual, clock=clock, hold=True)
    try:
        position = hold_buy(session, engine, data_provider, settings, candidate.symbol, sleeve_id=manual.id,
                            reason="review-queue buy (quality gate skipped by the person)")
    except MarketClosedError as exc:
        raise ReviewBuyMarketClosed(candidate.symbol) from exc
    except _ENGINE_REFUSALS as exc:
        raise ReviewBuyRefused(str(exc)) from exc
    except (AllProvidersFailedError, DataProviderError) as exc:
        raise ReviewBuyRefused(f"The buy needs fresh market data and could not get it: {exc}") from exc
    return _stamp_plan(session, position.trade_plan_id, candidate, SOURCE_REVIEW_BUY), position


def _finish_review_buy(session, candidate, manual, record, position, old_plan_id, clock):
    candidate = session.get(SmartMoneyCandidate, candidate.id)
    if old_plan_id is not None and old_plan_id != record.id:
        # The evaluation that sent it to the queue is superseded by the plan that was bought: the position is the
        # real record, so the old one must not stay in the missed-trades ledger as a trade nobody took.
        old = session.get(TradePlanRecord, old_plan_id)
        if old is not None and old.status in ("pending", "no_trade"):
            old.status = "discarded"
            session.add(old)
    candidate.status = STATUS_BOUGHT
    candidate.decided_by = "user"
    candidate.decided_at = _naive(clock())
    candidate.plan_id = record.id
    candidate.position_id = position.id
    candidate.sleeve_key = manual.key
    candidate.buy_queued_at = None
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return position


# --------------------------------------------------------------------------
# the market-open hand-back (called from automation_service.run_market_open_redos)
# --------------------------------------------------------------------------


def _retire_waiting_plan(session: Session, plan_id: int | None) -> None:
    plan = session.get(TradePlanRecord, plan_id) if plan_id else None
    if plan is not None and plan.status == "pending":
        plan.status = "discarded"
        session.add(plan)


def handle_redo(
    session: Session,
    settings: AppSettings,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    deferral: DeferredEvaluation,
    *,
    clock: Clock | None = None,
) -> str:
    """Carry out a deferral that has a candidate id, now that the market is open. Resolves the deferral row
    and returns a one-line outcome. The caller has already checked the redo window is open."""
    clock = resolve_clock(clock)
    now = _naive(clock())
    candidate = session.get(SmartMoneyCandidate, deferral.candidate_id) if deferral.candidate_id else None
    is_buy = deferral.source == SOURCE_REVIEW_BUY

    def finish(status: str, outcome: str) -> str:
        deferral.status = status
        deferral.outcome = outcome
        deferral.resolved_at = now
        session.add(deferral)
        session.commit()
        return outcome

    if candidate is None:
        return finish("skipped", "The smart-money candidate no longer exists.")
    wanted = STATUS_NEEDS_REVIEW if is_buy else STATUS_EVALUATING
    if candidate.status != wanted:
        return finish("skipped", f"The candidate is already {candidate.status}; nothing to redo.")

    deferral.attempts += 1
    when = format_market_time(now)
    try:
        if is_buy:
            position = execute_review_buy(session, candidate, settings, data_provider, llm_provider, clock=clock)
            _retire_waiting_plan(session, deferral.trade_plan_id)
            return finish("done", f"Redone at {when}: bought {candidate.symbol} (position #{position.id}) in the manual sleeve.")
        status = evaluate_candidate(
            session, candidate, settings, data_provider, llm_provider, clock=clock, after_open_wait=True
        )
        _retire_waiting_plan(session, deferral.trade_plan_id)
        return finish("done", f"Redone at {when}: {candidate.symbol} is now {status}.")
    except ReviewBuyRefused as exc:
        # The person's queued Buy cannot happen: the item goes back to waiting for them, with the reason.
        session.rollback()
        candidate = session.get(SmartMoneyCandidate, candidate.id)
        candidate.buy_queued_at = None
        candidate.reason_detail = f"The queued buy could not be made at the open: {exc}"[:500]
        session.add(candidate)
        session.commit()
        notify(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            f"Smart-money review: the queued buy of {candidate.symbol} could not be made at the open: {exc}",
        )
        return finish("done", f"Queued buy not made: {exc}")
    except ReviewBuyMarketClosed:
        session.rollback()
        deferral.status = PENDING  # the bell rang mid-run: try again on the next tick
        session.add(deferral)
        session.commit()
        return "market closed again"
    except Exception as exc:
        logger.exception("Smart-money redo failed for %s (attempt %d)", deferral.symbol, deferral.attempts)
        session.rollback()
        deferral = session.get(DeferredEvaluation, deferral.id)
        if deferral.attempts < MAX_REDO_ATTEMPTS:
            deferral.status = PENDING
            deferral.outcome = f"Attempt {deferral.attempts} of {MAX_REDO_ATTEMPTS} failed ({exc}); retrying on the next tick."
            session.add(deferral)
            session.commit()
            return deferral.outcome
        candidate = session.get(SmartMoneyCandidate, candidate.id)
        if not is_buy and candidate is not None and candidate.status == STATUS_EVALUATING:
            _skip(session, candidate, REASON_REDO_FAILED, f"{type(exc).__name__}: {exc}", now)
        elif candidate is not None:
            candidate.buy_queued_at = None
            session.add(candidate)
        return finish("failed", f"Couldn't be redone after {deferral.attempts} attempts: {exc}")


# --------------------------------------------------------------------------
# expiry
# --------------------------------------------------------------------------


def cancel_queued_deferrals(session: Session, candidate_id: int, why: str, now: datetime) -> None:
    for deferral in session.exec(
        select(DeferredEvaluation).where(DeferredEvaluation.candidate_id == candidate_id, DeferredEvaluation.status == PENDING)
    ).all():
        deferral.status = "skipped"
        deferral.outcome = why
        deferral.resolved_at = now
        session.add(deferral)


def expire_review_items(session: Session, now: datetime) -> list[SmartMoneyCandidate]:
    """needs_review items older than REVIEW_EXPIRY_DAYS become `expired` (nobody decided). They stay in the
    hindsight report as items nobody bought. Returns the rows expired."""
    now = _naive(now)
    rows = session.exec(
        select(SmartMoneyCandidate).where(SmartMoneyCandidate.status == STATUS_NEEDS_REVIEW, SmartMoneyCandidate.expires_at <= now)
    ).all()
    for row in rows:
        row.status = STATUS_EXPIRED
        row.decided_by = "auto"
        row.decided_at = now
        row.buy_queued_at = None
        cancel_queued_deferrals(session, row.id, "The review item expired before the queued buy ran.", now)
        session.add(row)
    if rows:
        session.commit()
    return list(rows)
