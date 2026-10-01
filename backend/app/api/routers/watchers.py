from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.deps import get_app_settings, get_session, require_auth
from app.config import AppSettings
from app.knowledge import FactKind, facts_known_as_of
from app.knowledge.models import KnownFact
from app.schemas.watcher_schemas import (
    WatcherEnabledRequest,
    WatcherEventSchema,
    WatcherRunResponse,
    WatchersResponse,
    WatcherStatusSchema,
)
from app.watchers.base import Watcher
from app.watchers.models import WatcherState
from app.watchers.registry import all_watchers, get_watcher
from app.watchers.runner import get_or_create_state, run_watcher_once

router = APIRouter(prefix="/api/watchers", tags=["watchers"], dependencies=[Depends(require_auth)])

# A manual run polls a live source and may start an evaluation: not a button to mash.
WATCHER_RUN_COOLDOWN_SECONDS = 30
_last_watcher_run_monotonic: dict[str, float] = {}

DEFAULT_EVENT_LIMIT = 50
MAX_EVENT_LIMIT = 200
RECENT_EVENTS_PER_WATCHER = 5


def _event_schema(fact: KnownFact) -> WatcherEventSchema:
    payload = fact.payload or {}
    return WatcherEventSchema(
        id=fact.id or 0,
        watcher=payload.get("watcher", fact.source),
        symbol=fact.symbol,
        kind=payload.get("kind", ""),
        headline=payload.get("headline", ""),
        severity=payload.get("severity", "info"),
        known_at=fact.known_at,
        source_ref=fact.source_ref,
        details=payload.get("details") or {},
        suppressed_reason=payload.get("suppressed_reason"),
    )


def _recent_events(session: Session, limit: int, watcher: str | None = None) -> list[WatcherEventSchema]:
    facts = facts_known_as_of(session, FactKind.WATCHER_EVENT)
    events = [_event_schema(f) for f in facts if watcher is None or f.source == watcher]
    return events[:limit]


def _status(session: Session, watcher: Watcher, state: WatcherState) -> WatcherStatusSchema:
    return WatcherStatusSchema(
        name=watcher.name,
        description=watcher.description,
        enabled=state.enabled,
        poll_interval_seconds=watcher.poll_interval_seconds,
        cooldown_seconds=watcher.cooldown_seconds,
        daily_fire_cap=watcher.daily_fire_cap,
        last_run_at=state.last_run_at,
        last_success_at=state.last_success_at,
        last_error=state.last_error,
        consecutive_failures=state.consecutive_failures,
        fires_today=state.fires_today,
        recent_events=_recent_events(session, RECENT_EVENTS_PER_WATCHER, watcher.name),
    )


@router.get("", response_model=WatchersResponse)
def list_watchers(
    session: Session = Depends(get_session), settings: AppSettings = Depends(get_app_settings)
) -> WatchersResponse:
    """Every installed watcher with its saved state. Read-only: it never creates
    a state row, polls or fires anything."""
    rows = [
        _status(session, watcher, session.get(WatcherState, watcher.name) or WatcherState(name=watcher.name))
        for watcher in all_watchers()
    ]
    return WatchersResponse(
        master_enabled=settings.watchers_enabled,
        action=settings.watchers_action,
        poll_minutes=settings.watchers_poll_minutes,
        watchers=rows,
    )


@router.get("/events", response_model=list[WatcherEventSchema])
def list_events(
    limit: int = Query(DEFAULT_EVENT_LIMIT, ge=1, le=MAX_EVENT_LIMIT), session: Session = Depends(get_session)
) -> list[WatcherEventSchema]:
    """The newest recorded watcher events, suppressed ones included."""
    return _recent_events(session, limit)


@router.put("/{name}", response_model=WatcherStatusSchema)
def set_watcher_enabled(
    name: str, req: WatcherEnabledRequest, session: Session = Depends(get_session)
) -> WatcherStatusSchema:
    """Turn one watcher on or off (on top of the master switch in Settings)."""
    watcher = get_watcher(name)
    if watcher is None:
        raise HTTPException(status_code=404, detail=f"No watcher named {name!r}")
    state = get_or_create_state(session, name)
    state.enabled = req.enabled
    session.add(state)
    session.commit()
    session.refresh(state)
    return _status(session, watcher, state)


@router.post("/{name}/run", response_model=WatcherRunResponse)
def run_watcher(
    name: str, session: Session = Depends(get_session), settings: AppSettings = Depends(get_app_settings)
) -> WatcherRunResponse:
    """Poll one watcher now (it follows every normal rule except 'not due yet')."""
    watcher = get_watcher(name)
    if watcher is None:
        raise HTTPException(status_code=404, detail=f"No watcher named {name!r}")
    now = time.monotonic()
    last = _last_watcher_run_monotonic.get(name)
    if last is not None and now - last < WATCHER_RUN_COOLDOWN_SECONDS:
        raise HTTPException(
            status_code=429,
            detail=f"{name} was just run: wait {WATCHER_RUN_COOLDOWN_SECONDS - (now - last):.0f}s before running it again.",
        )
    _last_watcher_run_monotonic[name] = now
    result = run_watcher_once(session, watcher, settings, force=True)
    return WatcherRunResponse(**result.__dict__)
