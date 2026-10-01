"""What a watcher is: a small poller that looks at one source and reports events.

A concrete watcher (a new SEC filing, a short-volume spike, a headline) subclasses
`Watcher`, sets its name and timings, and implements `poll()`. It does not write to
the database, send alerts or evaluate trades: the runner (`runner.py`) records each
event as a dated fact first, then applies cooldowns and the daily cap, then does what
the Watchers setting says. A watcher event never changes a trade's direction, size or
levels by itself. At most it triggers an ordinary full evaluation of the symbol.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlmodel import Session

from app.config import AppSettings

Severity = Literal["info", "notable", "urgent"]
SEVERITIES: tuple[str, ...] = ("info", "notable", "urgent")


@dataclass
class WatcherEvent:
    """One thing a watcher noticed.

    - `symbol`: the ticker it is about, or None for a market-wide event (an
      announcement that names no company). Market-wide events are recorded and
      alerted but cannot trigger an evaluation.
    - `kind`: a short slug for the type of event ("insider_buy", "filing_8k").
    - `known_at`: when the information became public (the source's own time,
      naive UTC). Not when the watcher happened to poll.
    - `source_ref`: a stable identifier for the item (an accession number or URL).
      With the watcher name and symbol it is how a repeat of the same event is
      recognised, so give every distinct item a distinct, stable value. A link
      starting with http(s) is also shown in the alert.
    """

    watcher: str
    symbol: str | None
    kind: str
    headline: str
    known_at: datetime
    source_ref: str | None = None
    severity: str = "info"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class WatcherContext:
    """What a watcher may use while polling. `now` is the runner's clock (naive
    UTC). The session is for reading (the watcher's own earlier facts, say); a
    watcher must not commit through it."""

    session: Session
    settings: AppSettings
    now: datetime


class Watcher(ABC):
    """Subclass, set the class attributes, implement `poll`, then call
    `register_watcher(MyWatcher())` once at import time."""

    # Unique and stable: it is the key of the watcher's saved state and part of
    # every event's identity.
    name: str = ""
    # One line for the Settings page.
    description: str = ""
    # How often the watcher is due. The scheduler wakes every few minutes; a watcher
    # is only polled once this many seconds have passed since its last run.
    poll_interval_seconds: int = 900
    # After an event fires for a symbol, further events for the same symbol from this
    # watcher are recorded but suppressed (no alert, no evaluation) for this long.
    cooldown_seconds: int = 3600
    # At most this many events per day (US Eastern date) may fire in total.
    daily_fire_cap: int = 20

    @abstractmethod
    def poll(self, context: WatcherContext) -> list[WatcherEvent]:
        """Fetch the source and return the events found. Synchronous. Return every
        recent item every time if that is easiest: the runner drops repeats. Raise
        on failure (the runner logs it and backs off); never return made-up events."""
