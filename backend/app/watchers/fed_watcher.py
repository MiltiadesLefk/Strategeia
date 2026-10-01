"""The Federal Reserve watcher: policy statements and speeches by the Chair and Board.

Every poll reads the Fed's public RSS feeds (`data_providers/fed_feed.py`), stores
each item as a dated fact (known_at = the item's own publication time), and only
then returns events. The runner records the events after that, so the facts exist
whatever happens to an alert.

Which items become events:

  * the FOMC statement itself (urgent);
  * the economic projections and the minutes of a meeting (notable);
  * a speech or testimony by a Chair (urgent) or by a Board member on the
    `GOVERNOR_SURNAMES` list (notable).

Everything else the feeds carry (discount-rate minutes, task-force notices, a
staff member's testimony) is stored and stays quiet.

A Fed event is a market-wide RISK FLAG, not a trade idea. It names no company, so
`symbol` is None, and the runner records and alerts such an event but can never
start an evaluation from it, whatever the Watchers action setting says. What a
statement means for any one stock is not decided here: nothing reads the words
(the hawkish or dovish tone is a documented, unbuilt seam in `fed_feed.py`).
The silent signal `fed_event_window` (analysis/fed_event_window.py) is the only
consumer, and it can only argue for caution.

An item older than EVENT_MAX_AGE is stored but never alerts, so the first run on an
empty database does not announce months of history.

Failure: if no feed could be read, the poll raises (the runner records the error and
backs off) and no event is produced. If only some feeds failed, the others'
events are returned and the failure is logged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from app.data_providers.base import DataProviderError
from app.data_providers.fed_feed import (
    TYPE_FOMC_MINUTES,
    TYPE_POLICY_STATEMENT,
    TYPE_PROJECTIONS,
    FedItem,
    fetch_fed_items,
    ingest_fed_item,
)
from app.data_providers.feed_http import fetch_feed
from app.watchers.base import Watcher, WatcherContext, WatcherEvent
from app.watchers.registry import get_watcher, register_watcher

logger = logging.getLogger(__name__)

WATCHER_NAME = "fed"

# The Fed publishes a handful of items a week. Fifteen minutes is quick enough for
# a statement (the market has moved within seconds anyway) and polite to a public feed.
POLL_INTERVAL_SECONDS = 15 * 60
# A statement day brings the statement, the projections and a press conference within
# a couple of hours; one alert covers the burst and the rest are recorded. Every Fed
# event is market-wide, so they all share one cooldown.
COOLDOWN_SECONDS = 60 * 60
DAILY_FIRE_CAP = 10
# A published item older than this is stored but never alerts.
EVENT_MAX_AGE = timedelta(hours=48)

# Of the monetary-policy documents, these alert (the statement is the urgent one).
_MONETARY_SEVERITY = {
    TYPE_POLICY_STATEMENT: "urgent",
    TYPE_PROJECTIONS: "notable",
    TYPE_FOMC_MINUTES: "notable",
}


def event_for_item(item: FedItem) -> WatcherEvent | None:
    """The event an item deserves, or None when it should only be stored. Pure:
    the rules alone decide, and the age check is the watcher's."""
    if item.category == "monetary_policy":
        severity = _MONETARY_SEVERITY.get(item.item_type)
        if severity is None:
            return None
        headline = f"Federal Reserve: {item.title}"
        kind = "fed_policy_statement" if item.item_type == TYPE_POLICY_STATEMENT else "fed_policy_document"
    elif item.is_chair or item.is_governor:
        severity = "urgent" if item.is_chair else "notable"
        what = "testimony" if item.category == "testimony" else "speech"
        role = "Chair" if item.is_chair else "Governor"
        headline = f"Fed {what} by {role} {item.speaker}: {item.title}"
        kind = "fed_speech"
    else:
        return None
    return WatcherEvent(
        watcher=WATCHER_NAME,
        symbol=None,
        kind=kind,
        headline=headline,
        known_at=item.published,
        source_ref=item.link,
        severity=severity,
        details={
            "item_type": item.item_type,
            "category": item.category,
            "speaker": item.speaker,
            "is_chair": item.is_chair,
            "published": item.published.isoformat(),
        },
    )


class FedWatcher(Watcher):
    name = WATCHER_NAME
    description = (
        "Federal Reserve statements, minutes and speeches by the Chair and Board members, from the Fed's own "
        "feeds. Market-wide: it alerts but never starts a trade evaluation. Everything is stored."
    )
    poll_interval_seconds = POLL_INTERVAL_SECONDS
    cooldown_seconds = COOLDOWN_SECONDS
    daily_fire_cap = DAILY_FIRE_CAP

    def __init__(self, *, fetch: Callable[[str], bytes] = fetch_feed) -> None:
        self._fetch = fetch

    def poll(self, context: WatcherContext) -> list[WatcherEvent]:
        fetched = fetch_fed_items(self._fetch)
        if fetched.feeds_ok == 0:
            raise DataProviderError("no Fed feed could be read: " + "; ".join(fetched.errors))
        for error in fetched.errors:
            logger.warning("fed watcher: one feed failed: %s", error)
        events: list[WatcherEvent] = []
        for item in fetched.items:
            ingest_fed_item(context.session, item)
            if context.now - item.published > EVENT_MAX_AGE:
                continue
            event = event_for_item(item)
            if event is not None:
                events.append(event)
        return events


def register_fed_watcher() -> None:
    """Install the watcher once. Called at application start-up, not at import."""
    if get_watcher(WATCHER_NAME) is None:
        register_watcher(FedWatcher())
