"""The watcher framework: recording, throttling, actions, the off-hours rule, the API.

No concrete watcher exists yet, so every test drives a fake one. Nothing here
touches the network: Telegram is a spy, trade-plan generation is a spy, and every
database is in memory.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_app_settings, get_session
from app.config import AppSettings
from app.knowledge import FactKind, as_of, facts_known_as_of
from app.main import app
from app.watchers import registry
from app.watchers.base import Watcher, WatcherContext, WatcherEvent
from app.watchers.models import WatcherState
from app.watchers.runner import (
    MAX_BACKOFF,
    backoff_delay,
    due_watchers,
    format_alert,
    run_due_watchers,
    run_watcher_once,
)

OPEN_NOW = datetime(2026, 9, 29, 15, 0)  # Tuesday 11:00 ET, mid-session
SUNDAY = datetime(2026, 9, 27, 15, 0)  # the market is shut
MONDAY_0950 = datetime(2026, 9, 28, 13, 50)  # 09:50 ET, after the redo delay


class FakeWatcher(Watcher):
    name = "fake"
    description = "A test double."
    poll_interval_seconds = 600
    cooldown_seconds = 3600
    daily_fire_cap = 3

    def __init__(self, events=None, error: Exception | None = None):
        self.events = events or []
        self.error = error
        self.polls = 0

    def poll(self, context: WatcherContext) -> list[WatcherEvent]:
        self.polls += 1
        if self.error is not None:
            raise self.error
        return list(self.events)


def event(symbol: str | None = "NVDA", ref: str = "https://example.test/a", headline: str = "Big news", **kw):
    return WatcherEvent(
        watcher="fake", symbol=symbol, kind="news", headline=headline, known_at=datetime(2026, 9, 29, 14, 0),
        source_ref=ref, **kw,
    )


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


class Spy:
    def __init__(self):
        self.alerts: list[str] = []
        self.order: list[str] = []

    def __call__(self, token, chat, text):
        self.order.append("alert")
        self.alerts.append(text)


def settings(**kw) -> AppSettings:
    base = dict(watchers_enabled=True, telegram_bot_token="tok", telegram_chat_id="chat")
    base.update(kw)
    return AppSettings(**base)


def run(session, watcher, st=None, now=OPEN_NOW, notifier=None, **kw):
    return run_watcher_once(session, watcher, st or settings(), now, notifier=notifier or Spy(), **kw)


def stored(session):
    return facts_known_as_of(session, FactKind.WATCHER_EVENT, as_of=datetime(2030, 1, 1))


# ------------------------------------------------------------------ recording


def test_event_is_recorded_before_the_alert_is_sent(session):
    seen_at_alert: list[int] = []

    def notifier(token, chat, text):
        seen_at_alert.append(len(stored(session)))

    result = run(session, FakeWatcher([event()]), notifier=notifier)

    assert result.fired == 1
    assert seen_at_alert == [1]  # the fact already existed when the alert went out
    [fact] = stored(session)
    assert fact.source == "fake" and fact.symbol == "NVDA"
    assert fact.known_at == datetime(2026, 9, 29, 14, 0)
    assert fact.payload["headline"] == "Big news" and fact.payload["suppressed_reason"] is None


def test_a_repeated_item_is_not_recorded_or_fired_twice(session):
    watcher = FakeWatcher([event()])
    spy = Spy()
    run(session, watcher, notifier=spy)
    again = run(session, watcher, now=OPEN_NOW + timedelta(hours=2), notifier=spy)

    assert again.duplicates == 1 and again.fired == 0
    assert len(spy.alerts) == 1 and len(stored(session)) == 1


def test_a_failed_alert_does_not_lose_the_event(session):
    def broken(token, chat, text):
        raise RuntimeError("telegram down")

    result = run(session, FakeWatcher([event()]), notifier=broken)

    assert result.fired == 1 and result.error is None
    assert len(stored(session)) == 1


# -------------------------------------------------------------------- actions


def test_record_mode_does_nothing_beyond_recording(session):
    spy = Spy()
    result = run(session, FakeWatcher([event()]), settings(watchers_action="record"), notifier=spy)

    assert result.fired == 1 and spy.alerts == []
    assert len(stored(session)) == 1


def test_alert_mode_sends_a_plain_text_alert_with_the_link(session):
    spy = Spy()
    run(session, FakeWatcher([event()]), settings(watchers_action="alert"), notifier=spy)

    [text] = spy.alerts
    assert "fake" in text and "NVDA" in text and "Big news" in text and "https://example.test/a" in text
    assert "tok" not in text and "chat" not in text  # nothing from the settings can leak in


def test_the_retired_alert_and_reevaluate_mode_just_alerts(session):
    """Swing re-evaluation is gone; a settings file that still says so only gets the alert."""
    spy = Spy()
    result = run(session, FakeWatcher([event()]), settings(watchers_action="alert_and_reevaluate"), notifier=spy)

    assert result.actions == ["alerted"] and len(spy.alerts) == 1


# ------------------------------------------------- cooldown, cap, suppression


def test_cooldown_suppresses_the_same_symbol_but_still_records(session):
    watcher = FakeWatcher([event(ref="a"), event(ref="b", headline="More news")])
    spy = Spy()
    result = run(session, watcher, notifier=spy)

    assert result.fired == 1 and result.suppressed == 1 and len(spy.alerts) == 1
    facts = {f.source_ref: f for f in stored(session)}
    assert facts["a"].payload["suppressed_reason"] is None
    assert "cooldown" in facts["b"].payload["suppressed_reason"]


def test_cooldown_ends_after_the_window_and_is_per_symbol(session):
    watcher = FakeWatcher([event(ref="a")])
    spy = Spy()
    run(session, watcher, notifier=spy)
    watcher.events = [event(symbol="AAPL", ref="c"), event(ref="d")]
    other = run(session, watcher, now=OPEN_NOW + timedelta(minutes=20), notifier=spy)
    assert other.fired == 1 and other.suppressed == 1  # AAPL fires, NVDA still cooling down

    watcher.events = [event(ref="e")]
    later = run(session, watcher, now=OPEN_NOW + timedelta(hours=2), notifier=spy)
    assert later.fired == 1


def test_daily_cap_suppresses_but_records_then_resets_next_day(session):
    watcher = FakeWatcher([event(symbol=s, ref=s) for s in ("A", "B", "C", "D")])
    spy = Spy()
    result = run(session, watcher, notifier=spy)

    assert result.fired == 3 and result.suppressed == 1
    d = next(f for f in stored(session) if f.symbol == "D")
    assert "daily cap" in d.payload["suppressed_reason"]

    watcher.events = [event(symbol="E", ref="E")]
    next_day = run(session, watcher, now=OPEN_NOW + timedelta(days=1), notifier=spy)
    assert next_day.fired == 1


# -------------------------------------------------------------------- failure


def test_a_failing_poll_is_caught_recorded_and_backed_off(session):
    watcher = FakeWatcher(error=RuntimeError("source down"))
    result = run(session, watcher)

    assert result.error and "source down" in result.error
    state = session.get(WatcherState, "fake")
    assert state.consecutive_failures == 1 and "source down" in state.last_error

    # Inside the backoff window the watcher is not polled again.
    skipped = run(session, watcher, now=OPEN_NOW + timedelta(seconds=watcher.poll_interval_seconds - 1))
    assert skipped.skipped_reason == "backing_off" and watcher.polls == 1

    # After it, a second failure doubles the wait.
    run(session, watcher, now=OPEN_NOW + timedelta(seconds=watcher.poll_interval_seconds + 1))
    assert session.get(WatcherState, "fake").consecutive_failures == 2
    assert backoff_delay(watcher, 2) == timedelta(seconds=2 * watcher.poll_interval_seconds)


def test_backoff_is_capped_and_a_success_clears_it(session):
    watcher = FakeWatcher()
    assert backoff_delay(watcher, 50) == MAX_BACKOFF
    assert backoff_delay(watcher, 0) == timedelta(0)

    watcher.error = RuntimeError("x")
    run(session, watcher)
    watcher.error = None
    run(session, watcher, now=OPEN_NOW + timedelta(hours=1))
    state = session.get(WatcherState, "fake")
    assert state.consecutive_failures == 0 and state.last_error is None and state.last_success_at is not None


# ------------------------------------------------------------ switches / clock


def test_master_switch_off_is_a_no_op(session):
    watcher = FakeWatcher([event()])
    result = run(session, watcher, settings(watchers_enabled=False))

    assert result.skipped_reason == "watchers_disabled" and watcher.polls == 0
    assert session.get(WatcherState, "fake") is None and stored(session) == []


def test_per_watcher_switch_off_is_a_no_op(session):
    session.add(WatcherState(name="fake", enabled=False))
    session.commit()
    watcher = FakeWatcher([event()])

    assert run(session, watcher).skipped_reason == "watcher_disabled" and watcher.polls == 0


def test_a_simulated_moment_never_runs_a_watcher(session):
    watcher = FakeWatcher([event()])
    with as_of(datetime(2025, 1, 2, 15, 0)):
        result = run(session, watcher)

    assert result.skipped_reason == "simulated_moment" and watcher.polls == 0


def test_not_due_is_skipped_unless_forced(session):
    watcher = FakeWatcher()
    run(session, watcher)
    soon = OPEN_NOW + timedelta(seconds=60)
    assert run(session, watcher, now=soon).skipped_reason == "not_due"
    assert run(session, watcher, now=soon, force=True).ran is True


def test_due_watchers_is_pure_and_honours_interval_backoff_and_switches():
    w = FakeWatcher()
    never = {}
    assert due_watchers([w], never, OPEN_NOW) == [w]
    ran = {"fake": WatcherState(name="fake", last_run_at=OPEN_NOW)}
    assert due_watchers([w], ran, OPEN_NOW + timedelta(seconds=599)) == []
    assert due_watchers([w], ran, OPEN_NOW + timedelta(seconds=600)) == [w]
    failing = {"fake": WatcherState(name="fake", last_run_at=OPEN_NOW, consecutive_failures=2)}
    assert due_watchers([w], failing, OPEN_NOW + timedelta(seconds=1199)) == []
    assert due_watchers([w], failing, OPEN_NOW + timedelta(seconds=1200)) == [w]
    off = {"fake": WatcherState(name="fake", enabled=False)}
    assert due_watchers([w], off, OPEN_NOW) == []
    assert due_watchers([w], never, OPEN_NOW, master_enabled=False) == []


def test_run_due_watchers_runs_only_the_due_ones(session):
    due, waiting = FakeWatcher(), FakeWatcher()
    waiting.name = "waiting"
    session.add(WatcherState(name="waiting", last_run_at=OPEN_NOW))
    session.commit()

    results = run_due_watchers(session, settings(), OPEN_NOW, watchers=[due, waiting], notifier=Spy())

    assert [r.watcher for r in results] == ["fake"]


def test_format_alert_has_no_link_line_for_a_non_url_ref():
    text = format_alert(event(ref="0001-23"), "")
    assert "0001-23" not in text


@pytest.fixture
def client(engine):
    def override_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_app_settings] = lambda: settings(watchers_action="record")
    watcher = FakeWatcher([event()])
    registry.register_watcher(watcher)
    try:
        yield TestClient(app), watcher
    finally:
        registry.unregister_watcher("fake")
        app.dependency_overrides.clear()


def test_get_watchers_is_read_only_and_empty_without_watchers(engine):
    def override_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_app_settings] = lambda: settings()
    try:
        body = TestClient(app).get("/api/watchers").json()
    finally:
        app.dependency_overrides.clear()
    assert body["watchers"] == [] and body["master_enabled"] is True and body["poll_minutes"] == 5


def test_get_routes_never_write(client, engine):
    c, _ = client
    assert c.get("/api/watchers").status_code == 200
    assert c.get("/api/watchers/events").json() == []
    with Session(engine) as s:
        assert s.exec(select(WatcherState)).all() == []


def test_run_then_read_back_and_cooldown(client):
    c, watcher = client
    first = c.post("/api/watchers/fake/run")
    assert first.status_code == 200 and first.json()["fired"] == 1
    assert c.post("/api/watchers/fake/run").status_code == 429  # run cooldown
    assert watcher.polls == 1

    listing = c.get("/api/watchers").json()["watchers"][0]
    assert listing["name"] == "fake" and listing["fires_today"] == 1 and listing["last_success_at"]
    assert listing["recent_events"][0]["headline"] == "Big news"
    events = c.get("/api/watchers/events?limit=5").json()
    assert len(events) == 1 and events[0]["symbol"] == "NVDA"


def test_unknown_watcher_is_404_and_limit_is_validated(client):
    c, _ = client
    assert c.post("/api/watchers/nope/run").status_code == 404
    assert c.put("/api/watchers/nope", json={"enabled": False}).status_code == 404
    assert c.get("/api/watchers/events?limit=0").status_code == 422


def test_disabling_one_watcher_stops_manual_runs(client):
    c, watcher = client
    assert c.put("/api/watchers/fake", json={"enabled": False}).json()["enabled"] is False
    assert c.post("/api/watchers/fake/run").json()["skipped_reason"] == "watcher_disabled"
    assert watcher.polls == 0


def test_registry_rejects_duplicates_and_bad_values():
    w = FakeWatcher()
    registry.register_watcher(w)
    try:
        with pytest.raises(ValueError):
            registry.register_watcher(FakeWatcher())
    finally:
        registry.unregister_watcher("fake")
    unnamed = FakeWatcher()
    unnamed.name = ""
    with pytest.raises(ValueError):
        registry.register_watcher(unnamed)


def test_settings_fields_are_validated():
    from pydantic import ValidationError

    from app.schemas.settings_schemas import SettingsUpdateRequest

    assert SettingsUpdateRequest(watchers_poll_minutes=60).watchers_poll_minutes == 60
    for bad in (0, 61):
        with pytest.raises(ValidationError):
            SettingsUpdateRequest(watchers_poll_minutes=bad)
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(watchers_action="trade")
    defaults = AppSettings()
    assert defaults.watchers_enabled is False and defaults.watchers_action == "alert"
