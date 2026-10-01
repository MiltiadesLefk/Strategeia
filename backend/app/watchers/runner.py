"""Running watchers: poll, record, throttle, then act.

The order inside one run is deliberate:
1. Poll the source (any exception is caught, logged and saved as the watcher's last
   error; the runner never raises into the scheduler).
2. Record every event as a dated fact BEFORE anything else happens to it, so the
   record exists even if an alert or evaluation then fails, and a backtest can ask
   what a watcher knew and when. A repeat of an item already recorded is dropped here.
3. Apply the per-symbol cooldown and the daily cap. A throttled event is still
   recorded (with the reason) but does nothing else.
4. Do what the Watchers setting says: nothing more, a Telegram alert, or an alert
   plus an ordinary full evaluation of the symbol (see reevaluate.py).
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.knowledge import FactKind, is_simulated, make_dedupe_key, record_fact
from app.llm_providers.base import LLMProvider
from app.markets import to_market_time
from app.portfolio.engine import Clock
from app.services.telegram_service import notify
from app.timeutil import utcnow_naive
from app.watchers.base import SEVERITIES, Watcher, WatcherContext, WatcherEvent
from app.watchers.models import WatcherState
from app.watchers.reevaluate import ReevaluationOutcome, reevaluate_for_event
from app.watchers.registry import all_watchers

logger = logging.getLogger(__name__)

# After a failed poll the watcher waits poll_interval * 2^(failures-1) before it is
# tried again, never longer than this. A source that is down is not hammered, and a
# watcher that failed once is retried soon rather than after a long fixed pause.
MAX_BACKOFF = timedelta(hours=6)

MAX_ERROR_LENGTH = 500
MAX_ALERT_HEADLINE_LENGTH = 300
WILDCARD_SYMBOL = "*"  # key for market-wide events in WatcherState's per-symbol maps

SKIP_SIMULATED = "simulated_moment"
SKIP_MASTER_OFF = "watchers_disabled"
SKIP_WATCHER_OFF = "watcher_disabled"
SKIP_BACKOFF = "backing_off"
SKIP_NOT_DUE = "not_due"

Notifier = Callable[[str, str, str], None]
Reevaluator = Callable[..., ReevaluationOutcome]


@dataclass
class WatcherRunResult:
    watcher: str
    ran: bool = False
    skipped_reason: str | None = None
    error: str | None = None
    new_events: int = 0
    duplicates: int = 0
    fired: int = 0
    suppressed: int = 0
    actions: list[str] = field(default_factory=list)


def _market_day(now: datetime) -> str:
    return to_market_time(now).date().isoformat()


def get_or_create_state(session: Session, name: str) -> WatcherState:
    state = session.get(WatcherState, name)
    if state is None:
        state = WatcherState(name=name)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def backoff_delay(watcher: Watcher, consecutive_failures: int) -> timedelta:
    if consecutive_failures <= 0:
        return timedelta(0)
    exponent = min(consecutive_failures - 1, 20)
    return min(MAX_BACKOFF, timedelta(seconds=watcher.poll_interval_seconds * (2**exponent)))


def next_due_at(watcher: Watcher, state: WatcherState) -> datetime | None:
    """When this watcher may next be polled (None: never run, due now). A failed
    watcher waits its backoff instead of its normal interval."""
    if state.last_run_at is None:
        return None
    if state.consecutive_failures > 0:
        return state.last_run_at + backoff_delay(watcher, state.consecutive_failures)
    return state.last_run_at + timedelta(seconds=watcher.poll_interval_seconds)


def due_watchers(
    watchers: list[Watcher], states: dict[str, WatcherState], now: datetime, *, master_enabled: bool = True
) -> list[Watcher]:
    """The watchers the scheduler should run at `now`: enabled, and past their
    interval (or their backoff, after failures). Pure: it reads, never writes."""
    if not master_enabled:
        return []
    due: list[Watcher] = []
    for watcher in watchers:
        state = states.get(watcher.name)
        if state is None:
            due.append(watcher)
            continue
        if not state.enabled:
            continue
        at = next_due_at(watcher, state)
        if at is None or at <= now:
            due.append(watcher)
    return due


def _suppression_reason(watcher: Watcher, state: WatcherState, symbol_key: str, now: datetime, day: str) -> str | None:
    fires_today = state.fires_today if state.fires_day == day else 0
    if fires_today >= watcher.daily_fire_cap:
        return f"daily cap of {watcher.daily_fire_cap} reached"
    last = state.last_fired_at.get(symbol_key)
    if last and watcher.cooldown_seconds > 0:
        try:
            last_at = datetime.fromisoformat(last)
        except ValueError:
            last_at = None
        if last_at is not None and now < last_at + timedelta(seconds=watcher.cooldown_seconds):
            return f"cooldown: {symbol_key} already fired within the last {watcher.cooldown_seconds // 60} minutes"
    return None


def format_alert(event: WatcherEvent, action_note: str) -> str:
    """Plain text for Telegram: what happened, where to read it, what happens next.
    Built from the event only: nothing from settings, so no secret can leak into it."""
    head = f"Watcher {event.watcher}" + (f" - {event.symbol}" if event.symbol else "")
    lines = [head, event.headline[:MAX_ALERT_HEADLINE_LENGTH]]
    if event.source_ref and event.source_ref.lower().startswith(("http://", "https://")):
        lines.append(event.source_ref)
    if action_note:
        lines.append(action_note)
    return "\n".join(lines)


def _clean_event(event: WatcherEvent, watcher: Watcher) -> WatcherEvent:
    event.watcher = watcher.name
    if event.symbol:
        event.symbol = event.symbol.strip().upper() or None
    if event.severity not in SEVERITIES:
        event.severity = "info"
    return event


def run_watcher_once(
    session: Session,
    watcher: Watcher,
    settings: AppSettings,
    now: datetime | None = None,
    *,
    force: bool = False,
    data_provider: DataProvider | None = None,
    llm_provider: LLMProvider | None = None,
    notifier: Notifier = notify,
    reevaluator: Reevaluator = reevaluate_for_event,
    clock: Clock | None = None,
) -> WatcherRunResult:
    """One poll of one watcher, start to finish. Never raises.

    `force` (the manual Run button) skips the not-due and backoff checks but not
    the master switch, the per-watcher switch, the simulated-moment rule, the
    cooldown or the daily cap. Providers are built only if an evaluation is
    actually needed."""
    result = WatcherRunResult(watcher=watcher.name)
    now = now if now is not None else utcnow_naive()
    try:
        if is_simulated():
            # A backtest is replaying history: a watcher polls live sources, which
            # would put the present into the past.
            result.skipped_reason = SKIP_SIMULATED
            return result
        if not settings.watchers_enabled:
            result.skipped_reason = SKIP_MASTER_OFF
            return result
        state = get_or_create_state(session, watcher.name)
        if not state.enabled:
            result.skipped_reason = SKIP_WATCHER_OFF
            return result
        if not force:
            due_at = next_due_at(watcher, state)
            if due_at is not None and due_at > now:
                result.skipped_reason = SKIP_BACKOFF if state.consecutive_failures > 0 else SKIP_NOT_DUE
                return result
        return _run(session, watcher, settings, state, now, result, data_provider, llm_provider, notifier, reevaluator, clock)
    except Exception as exc:  # the scheduler must never see an exception from here
        logger.exception("Watcher %s: run failed", watcher.name)
        result.error = str(exc)[:MAX_ERROR_LENGTH]
        try:
            session.rollback()
        except Exception:
            logger.exception("Watcher %s: rollback failed", watcher.name)
        return result


def _run(
    session: Session,
    watcher: Watcher,
    settings: AppSettings,
    state: WatcherState,
    now: datetime,
    result: WatcherRunResult,
    data_provider: DataProvider | None,
    llm_provider: LLMProvider | None,
    notifier: Notifier,
    reevaluator: Reevaluator,
    clock: Clock | None,
) -> WatcherRunResult:
    result.ran = True
    state.last_run_at = now
    try:
        events = list(watcher.poll(WatcherContext(session=session, settings=settings, now=now)))
    except Exception as exc:
        logger.exception("Watcher %s: poll failed", watcher.name)
        state.consecutive_failures += 1
        state.last_error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
        session.add(state)
        session.commit()
        result.error = state.last_error
        return result

    state.consecutive_failures = 0
    state.last_error = None
    state.last_success_at = now
    day = _market_day(now)
    if state.fires_day != day:
        state.fires_day = day
        state.fires_today = 0
    session.add(state)
    session.commit()

    for raw in events:
        event = _clean_event(raw, watcher)
        symbol_key = event.symbol or WILDCARD_SYMBOL
        reason = _suppression_reason(watcher, state, symbol_key, now, day)
        recorded = record_fact(
            session,
            kind=FactKind.WATCHER_EVENT,
            source=watcher.name,
            symbol=event.symbol,
            source_ref=event.source_ref,
            dedupe_key=make_dedupe_key(watcher.name, event.source_ref or event.headline, event.symbol),
            known_at=event.known_at,
            payload={
                "watcher": watcher.name,
                "kind": event.kind,
                "headline": event.headline,
                "severity": event.severity,
                "details": event.details,
                "suppressed_reason": reason,
            },
        )
        if not recorded.created:
            result.duplicates += 1
            continue
        result.new_events += 1
        if reason is not None:
            result.suppressed += 1
            continue

        # It fires: count it against the cap and start the symbol's cooldown first, so
        # even if the action below fails the event is not fired a second time.
        state.fires_today += 1
        state.last_fired_at = {**state.last_fired_at, symbol_key: now.isoformat()}
        state.last_event_keys = {**state.last_event_keys, symbol_key: event.source_ref or ""}
        session.add(state)
        session.commit()
        result.fired += 1
        _act(session, settings, watcher, event, now, result, data_provider, llm_provider, notifier, reevaluator, clock)
    return result


def _act(
    session: Session,
    settings: AppSettings,
    watcher: Watcher,
    event: WatcherEvent,
    now: datetime,
    result: WatcherRunResult,
    data_provider: DataProvider | None,
    llm_provider: LLMProvider | None,
    notifier: Notifier,
    reevaluator: Reevaluator,
    clock: Clock | None,
) -> None:
    action = settings.watchers_action
    if action == "record":
        result.actions.append("recorded")
        return

    outcome: ReevaluationOutcome | None = None
    if action == "alert_and_reevaluate" and event.symbol:
        try:
            if data_provider is None or llm_provider is None:
                from app.data_providers.factory import get_data_provider
                from app.llm_providers.factory import get_llm_provider

                data_provider = data_provider or get_data_provider(settings)
                llm_provider = llm_provider or get_llm_provider(settings)
            outcome = reevaluator(session, settings, event, now, data_provider, llm_provider, clock=clock)
        except Exception as exc:
            logger.exception("Watcher %s: re-evaluation of %s failed", watcher.name, event.symbol)
            session.rollback()
            outcome = ReevaluationOutcome("failed", f"The evaluation of {event.symbol} failed: {exc}")
        result.actions.append(f"reevaluate:{outcome.status}")

    try:
        notifier(settings.telegram_bot_token, settings.telegram_chat_id, format_alert(event, outcome.detail if outcome else ""))
        result.actions.append("alerted")
    except Exception:
        logger.exception("Watcher %s: alert failed", watcher.name)


def run_due_watchers(
    session: Session,
    settings: AppSettings,
    now: datetime | None = None,
    *,
    watchers: list[Watcher] | None = None,
    jitter_seconds: float = 0.0,
    **kwargs,
) -> list[WatcherRunResult]:
    """What the scheduler tick calls: run every watcher that is due. A small random
    pause (up to `jitter_seconds`) before each spreads the sources' load."""
    now = now if now is not None else utcnow_naive()
    candidates = watchers if watchers is not None else all_watchers()
    states = {row.name: row for row in session.exec(select(WatcherState)).all()}
    results: list[WatcherRunResult] = []
    for watcher in due_watchers(candidates, states, now, master_enabled=settings.watchers_enabled):
        if jitter_seconds > 0:
            time.sleep(random.uniform(0, jitter_seconds))
        results.append(run_watcher_once(session, watcher, settings, now, **kwargs))
    return results
