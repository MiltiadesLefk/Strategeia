"""The Fed feeds, the Fed watcher and the fed_event_window silent signal.

The feeds are saved excerpts of the real ones (tests/fixtures/fed_feed); nothing
touches the network. The reference moment is the evening of 1 Oct 2026, a few hours
after the newest speech in the excerpts.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from app.analysis import shadow_signals
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis import fed_event_window
from app.analysis.shadow_signals import ShadowContext, evaluate_shadow_signals, registered_shadow_signals
from app.config import AppSettings
from app.data_providers import fed_feed
from app.data_providers.base import DataProviderError
from app.data_providers.fed_feed import (
    FEED_BASE,
    backfill_fed,
    classify_item,
    fetch_fed_items,
    ingest_fed_item,
    parse_fed_feed,
    parse_pub_date,
    speaker_from_title,
)
from app.knowledge import FactKind, KnownFact, as_of, facts_known_as_of
from app.watchers import fed_watcher, registry
from app.watchers.fed_watcher import FedWatcher, event_for_item, register_fed_watcher
from app.watchers.models import WatcherState
from app.watchers.runner import run_watcher_once

FIXTURES = Path(__file__).parent / "fixtures" / "fed_feed"
NOW = datetime(2026, 10, 1, 20, 0)
STATEMENT_TIME = datetime(2026, 9, 16, 18, 0)


def feed_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def serving(**overrides):
    """A fetch function answering from the saved excerpts; `overrides` maps a feed
    file name to bytes or to an exception to raise."""

    def fetch(url: str) -> bytes:
        name = url.rsplit("/", 1)[-1]
        chosen = overrides.get(name, None)
        if isinstance(chosen, Exception):
            raise chosen
        return chosen if chosen is not None else feed_bytes(name)

    return fetch


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class Spy:
    def __init__(self):
        self.alerts: list[str] = []

    def __call__(self, token, chat, text):
        self.alerts.append(text)


class Reevals:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        raise AssertionError("a Fed event must never start an evaluation")


def run(session, watcher, now=NOW, notifier=None, reevaluator=None, action="alert_and_reevaluate"):
    return run_watcher_once(
        session,
        watcher,
        AppSettings(watchers_enabled=True, watchers_action=action, telegram_bot_token="t", telegram_chat_id="c"),
        now,
        data_provider=object(),
        llm_provider=object(),
        notifier=notifier or Spy(),
        reevaluator=reevaluator or Reevals(),
    )


def stored_items(session):
    return facts_known_as_of(session, FactKind.FED_SPEECH, as_of=datetime(2030, 1, 1))


# ------------------------------------------------------------------ parsing


@pytest.fixture(autouse=True)
def _signals_read_as_shadow(request, monkeypatch):
    """These tests are about how a signal is read and recorded. The signals were promoted to real scoring
    (analysis/live_evidence.py), so run the shadow loop as if none were live."""
    if request.node.get_closest_marker("promoted") is None:
        monkeypatch.setattr(shadow_signals, "LIVE_SIGNALS", set())


def test_the_monetary_feed_parses_with_utc_times_and_types():
    items = parse_fed_feed(feed_bytes("press_monetary.xml"), "monetary_policy")
    assert len(items) == 5
    statement = items[0]
    assert statement.title == "Federal Reserve issues FOMC statement"
    # "Wed, 16 Sep 2026 18:00:00 GMT" is 14:00 New York time; the stored time is UTC.
    assert statement.published == STATEMENT_TIME
    assert statement.published.tzinfo is None
    assert statement.link.endswith("monetary20260916a.htm")
    assert [i.item_type for i in items] == [
        "policy_statement",
        "projections",
        "other_monetary",  # discount-rate minutes
        "fomc_minutes",
        "fomc_minutes",
    ]
    assert all(i.speaker is None for i in items)


def test_speeches_carry_the_speaker_and_the_chair_flag():
    speeches = parse_fed_feed(feed_bytes("speeches.xml"), "speech")
    assert [s.speaker for s in speeches] == ["Bowman", "Jefferson", "Waller", "Cook", "Waller", "Warsh"]
    assert [s.is_chair for s in speeches] == [False, False, False, False, False, True]
    assert speeches[0].is_governor and not speeches[0].is_chair
    assert speeches[0].published == datetime(2026, 10, 1, 19, 0)
    # entities in the one-line description are decoded
    assert "CEO & Senior Management Summit" in speeches[0].description


def test_testimony_keeps_its_own_category_and_unknown_speakers_are_not_officials():
    items = parse_fed_feed(feed_bytes("testimony.xml"), "testimony")
    by_speaker = {i.speaker: i for i in items}
    assert all(i.category == "testimony" for i in items)
    assert by_speaker["Warsh"].is_chair
    assert by_speaker["Gibson"].speaker == "Gibson"
    assert not by_speaker["Gibson"].is_chair and not by_speaker["Gibson"].is_governor


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Waller, Payments in the Age of AI Agents", "Waller"),
        ("Powell, Semiannual Monetary Policy Report to the Congress", "Powell"),
        ("O'Neill, Remarks", "O'Neill"),
        ("Remarks at the conference, with thanks", None),
        ("Federal Reserve issues FOMC statement", None),
    ],
)
def test_speaker_comes_from_the_start_of_the_title(title, expected):
    assert speaker_from_title(title) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Thu, 1 Oct 2026 19:00:00 GMT", datetime(2026, 10, 1, 19, 0)),
        ("Thu, 1 Oct 2026 15:00:00 -0400", datetime(2026, 10, 1, 19, 0)),
        ("Thu, 01 Oct 2026 19:00:00 +0000", datetime(2026, 10, 1, 19, 0)),
        ("Thu, 1 Oct 2026 19:00:00", None),  # no zone: not placed on the clock by guessing
        ("not a date", None),
        ("", None),
        (None, None),
    ],
)
def test_publication_times_need_a_zone(raw, expected):
    assert parse_pub_date(raw) == expected


def test_malformed_items_are_skipped_not_fatal():
    xml = b"""<?xml version="1.0"?><rss><channel>
      <item><title>Waller, Good</title><link>https://x.test/a</link><pubDate>Thu, 1 Oct 2026 19:00:00 GMT</pubDate></item>
      <item><title></title><link>https://x.test/b</link><pubDate>Thu, 1 Oct 2026 19:00:00 GMT</pubDate></item>
      <item><title>No link</title><pubDate>Thu, 1 Oct 2026 19:00:00 GMT</pubDate></item>
      <item><title>No time</title><link>https://x.test/c</link></item>
      <item><title>Zoneless</title><link>https://x.test/d</link><pubDate>Thu, 1 Oct 2026 19:00:00</pubDate></item>
    </channel></rss>"""
    [item] = parse_fed_feed(xml, "speech")
    assert item.title == "Waller, Good" and item.speaker == "Waller"


def test_a_body_that_is_not_xml_raises():
    with pytest.raises(DataProviderError):
        parse_fed_feed(b"<html><body>Access denied</body>", "speech")
    with pytest.raises(DataProviderError, match="DOCTYPE"):
        parse_fed_feed(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss/>', "speech")


def test_classify_item():
    assert classify_item("monetary_policy", "Federal Reserve issues FOMC statement") == "policy_statement"
    assert classify_item("monetary_policy", "Minutes of the Federal Open Market Committee, July 28-29, 2026") == "fomc_minutes"
    assert classify_item("monetary_policy", "Minutes of the Board's discount rate meetings on June 8") == "other_monetary"
    assert classify_item("speech", "Federal Reserve issues FOMC statement") == "speech"


# ------------------------------------------------------------------ dated facts


def test_ingest_stores_a_market_wide_fact_known_at_the_publication_time(session):
    item = parse_fed_feed(feed_bytes("press_monetary.xml"), "monetary_policy")[0]
    recorded = ingest_fed_item(session, item)
    assert recorded.created
    fact = recorded.fact
    assert fact.kind == "fed_speech" and fact.symbol is None
    assert fact.known_at == STATEMENT_TIME and fact.known_at_basis == "source"
    assert fact.effective_at == datetime(2026, 9, 16)
    assert fact.source_ref == item.link
    assert fact.payload["item_type"] == "policy_statement"
    assert fact.payload["title"] == "Federal Reserve issues FOMC statement"


def test_ingest_is_idempotent(session):
    item = parse_fed_feed(feed_bytes("speeches.xml"), "speech")[0]
    assert ingest_fed_item(session, item).created
    assert not ingest_fed_item(session, item).created
    assert len(session.exec(select(KnownFact)).all()) == 1


def test_a_backtest_cannot_see_an_item_published_after_its_moment(session):
    backfill_fed(session, serving())
    with as_of(datetime(2026, 9, 16, 17, 59)):
        before = [f.payload["title"] for f in facts_known_as_of(session, FactKind.FED_SPEECH)]
    with as_of(datetime(2026, 9, 16, 18, 0)):
        at = [f.payload["title"] for f in facts_known_as_of(session, FactKind.FED_SPEECH)]
    assert "Federal Reserve issues FOMC statement" not in before
    assert "Federal Reserve issues FOMC statement" in at


def test_backfill_loads_every_feed_and_resumes_after_a_failure(session):
    first = backfill_fed(session, serving(**{"speeches.xml": DataProviderError("HTTP 503")}))
    assert first.feeds_ok == 2 and len(first.errors) == 1 and "speeches.xml" in first.errors[0]
    assert first.created == 9  # 5 monetary + 4 testimony
    assert len(stored_items(session)) == 9

    second = backfill_fed(session, serving())
    assert second.errors == []
    assert second.created == 6 and second.already_stored == 9  # only the speeches were missing
    third = backfill_fed(session, serving())
    assert third.created == 0 and third.already_stored == 15
    assert len(stored_items(session)) == 15


# ------------------------------------------------------------------ event rules


def items_by_title(name: str, category: str):
    return {i.title: i for i in parse_fed_feed(feed_bytes(name), category)}


def test_rules_decide_which_items_alert():
    monetary = items_by_title("press_monetary.xml", "monetary_policy")
    statement = event_for_item(monetary["Federal Reserve issues FOMC statement"])
    assert statement.severity == "urgent" and statement.symbol is None and statement.kind == "fed_policy_statement"
    assert statement.known_at == STATEMENT_TIME and statement.source_ref.endswith("monetary20260916a.htm")

    projections = [i for i in monetary.values() if i.item_type == "projections"][0]
    assert event_for_item(projections).severity == "notable"
    minutes = [i for i in monetary.values() if i.item_type == "fomc_minutes"][0]
    assert event_for_item(minutes).severity == "notable"
    quiet = [i for i in monetary.values() if i.item_type == "other_monetary"]
    assert quiet and all(event_for_item(i) is None for i in quiet)

    speeches = items_by_title("speeches.xml", "speech")
    chair = event_for_item(speeches["Warsh, In Our Time"])
    assert chair.severity == "urgent" and "Chair Warsh" in chair.headline
    governor = event_for_item(speeches["Waller, Payments in the Age of AI Agents"])
    assert governor.severity == "notable" and "Governor Waller" in governor.headline

    testimony = items_by_title("testimony.xml", "testimony")
    unknown = [i for i in testimony.values() if i.speaker == "Gibson"][0]
    assert event_for_item(unknown) is None  # stored, never alerts
    hearing = event_for_item([i for i in testimony.values() if i.speaker == "Warsh"][0])
    assert hearing.severity == "urgent" and "testimony" in hearing.headline


# ------------------------------------------------------------------ the watcher


def test_a_poll_stores_everything_and_alerts_only_on_fresh_important_items(session):
    spy = Spy()
    # Two hours after the Bowman and Jefferson speeches; the 16 Sep statement is two weeks old.
    result = run(session, FedWatcher(fetch=serving()), notifier=spy)
    assert result.error is None and result.ran
    assert len(stored_items(session)) == 15  # all stored, including the stale and unimportant ones
    # Fresh within 48 h: the speeches of 1 Oct (Bowman, Jefferson, Waller, ...). Only Governors on the list alert.
    events = facts_known_as_of(session, FactKind.WATCHER_EVENT, as_of=datetime(2030, 1, 1))
    assert events and all(f.symbol is None and f.source == "fed" for f in events)
    assert all("2026-09-16" not in (f.payload["details"].get("published") or "") for f in events)
    assert result.fired == 1  # one shared market-wide cooldown: the rest are recorded, suppressed
    assert result.suppressed == result.new_events - 1
    assert len(spy.alerts) == 1


def test_a_policy_statement_alerts_but_never_evaluates_anything(session):
    reevals, spy = Reevals(), Spy()
    # The evening of the statement: only the statement-day items are fresh.
    result = run(session, FedWatcher(fetch=serving()), now=datetime(2026, 9, 16, 22, 0), notifier=spy, reevaluator=reevals)
    assert result.fired == 1 and reevals.calls == []
    assert "reevaluate" not in " ".join(result.actions)
    [alert] = spy.alerts
    assert "Federal Reserve issues FOMC statement" in alert
    assert "federalreserve.gov" in alert


def test_the_same_items_are_not_fired_twice(session):
    watcher = FedWatcher(fetch=serving())
    now = datetime(2026, 9, 16, 22, 0)
    first = run(session, watcher, now=now)
    second = run(session, watcher, now=now + timedelta(minutes=16))
    assert first.fired == 1
    assert second.new_events == 0 and second.fired == 0 and second.duplicates == first.new_events


def test_a_stale_item_is_stored_but_never_alerts(session):
    result = run(session, FedWatcher(fetch=serving()), now=datetime(2026, 12, 1, 12, 0))
    assert result.new_events == 0 and result.fired == 0
    assert len(stored_items(session)) == 15


def test_every_feed_failing_is_a_recorded_error_with_no_events(session):
    fail = DataProviderError("feed fetch failed: HTTP 403")
    watcher = FedWatcher(fetch=serving(**{n: fail for n in fed_feed.FEEDS}))
    result = run(session, watcher)
    assert result.ran and result.fired == 0 and result.new_events == 0
    assert "no Fed feed could be read" in result.error and "HTTP 403" in result.error
    state = session.get(WatcherState, "fed")
    assert state.consecutive_failures == 1 and "HTTP 403" in state.last_error
    assert stored_items(session) == []


def test_one_failing_feed_does_not_lose_the_others(session):
    watcher = FedWatcher(fetch=serving(**{"press_monetary.xml": DataProviderError("HTTP 500")}))
    result = run(session, watcher)
    assert result.error is None and result.fired == 1
    assert len(stored_items(session)) == 10  # speeches and testimony


def test_the_watcher_is_registered_once_and_is_market_wide():
    registry.unregister_watcher("fed")
    try:
        register_fed_watcher()
        register_fed_watcher()
        watcher = registry.get_watcher("fed")
        assert isinstance(watcher, FedWatcher)
        assert watcher.poll_interval_seconds == 15 * 60
    finally:
        registry.unregister_watcher("fed")


# ------------------------------------------------------------------ the silent signal


def seeded(session):
    backfill_fed(session, serving())
    return session


@pytest.mark.parametrize(
    "gap, inside",
    [
        (timedelta(0), True),
        (timedelta(hours=23, minutes=59), True),
        (timedelta(days=1), True),  # the edge counts
        (timedelta(days=1, seconds=1), False),
        (-timedelta(hours=12), True),  # an event in the future is inside too (pure maths)
        (-timedelta(days=2), False),
    ],
)
def test_window_maths_is_symmetric_around_the_moment(gap, inside):
    moment = datetime(2026, 9, 17, 18, 0)
    assert fed_event_window.within_window(moment - gap, moment) is inside


def test_the_signal_is_registered_and_penalty_only(session):
    assert "fed_event_window" in registered_shadow_signals()
    seeded(session)
    # 6 hours after the statement: flagged, -1, whichever way the trade goes.
    for direction in ("long", "short", None):
        signal = fed_event_window.build_fed_window_signal(session, STATEMENT_TIME + timedelta(hours=6))
        assert signal.available and signal.value == "1" and signal.would_score == -1
        assert "risk flag" in signal.reason
    # And through the registry's own entry point, inside a simulated moment.
    with as_of(STATEMENT_TIME + timedelta(hours=6)):
        [signal] = [s for s in evaluate_shadow_signals(ShadowContext("AAPL", "long", session)) if s.name == "fed_event_window"]
    assert signal.would_score == -1


def test_a_chair_speech_flags_but_a_governor_speech_does_not(session):
    seeded(session)
    # Warsh's speech is the oldest of the speeches in the excerpt; read it from its own day.
    warsh = [i for i in parse_fed_feed(feed_bytes("speeches.xml"), "speech") if i.speaker == "Warsh"][0]
    flagged = fed_event_window.build_fed_window_signal(session, warsh.published + timedelta(hours=2))
    assert flagged.value == "1" and "In Our Time" in flagged.reason
    # 1 Oct evening: only governors spoke in the last day, and the last statement was two weeks earlier.
    quiet = fed_event_window.build_fed_window_signal(session, NOW)
    assert quiet.available and quiet.value == "0" and quiet.would_score == 0


def test_outside_the_window_it_reads_zero(session):
    seeded(session)
    signal = fed_event_window.build_fed_window_signal(session, STATEMENT_TIME + timedelta(days=3))
    assert signal.available and signal.value == "0" and signal.would_score == 0


def test_a_future_statement_is_invisible_to_the_signal(session):
    seeded(session)
    # One hour BEFORE the statement was published: not known yet, so not flagged.
    signal = fed_event_window.build_fed_window_signal(session, STATEMENT_TIME - timedelta(hours=1))
    assert signal.value != "1"


def test_unavailable_without_data_or_a_session(session):
    assert not fed_event_window.build_fed_window_signal(session, NOW).available
    assert not fed_event_window.build_fed_window_signal(None, NOW).available
    # Facts exist, but none known as of a moment before the oldest item: still "never loaded".
    seeded(session)
    assert not fed_event_window.build_fed_window_signal(session, datetime(2020, 1, 1)).available
