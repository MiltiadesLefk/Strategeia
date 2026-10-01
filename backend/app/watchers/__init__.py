"""Watchers: background pollers that turn something new in a source into a recorded event.

To add one, subclass `Watcher` (base.py), implement `poll(context) -> list[WatcherEvent]`
and call `register_watcher(MyWatcher())` at import time. The runner (runner.py) handles
recording, cooldowns, the daily cap, alerts and the optional evaluation.
"""

from app.watchers.base import SEVERITIES, Watcher, WatcherContext, WatcherEvent
from app.watchers.registry import all_watchers, get_watcher, register_watcher

__all__ = [
    "SEVERITIES",
    "Watcher",
    "WatcherContext",
    "WatcherEvent",
    "all_watchers",
    "get_watcher",
    "register_watcher",
]
