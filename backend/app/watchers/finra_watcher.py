"""The FINRA short-volume watcher: keeps the daily short-volume files up to date.

FINRA publishes each trading day's file after the close. Every poll brings the last
few days of the watchlist up to date through `refresh_finra_short_volume`, which only
downloads days not stored yet and stores each day as a dated fact. Re-running is
harmless.

This watcher never raises an event. A short-volume ratio is a quiet input to the silent
signal, not news; it is stored and nothing is announced. If every day checked failed to
download (and nothing was already stored), the poll raises so the runner records the
error and backs off.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.data_providers.base import DataProviderError
from app.data_providers.finra_provider import FinraProvider
from app.signals.finra import refresh_finra_short_volume
from app.watchers.base import Watcher, WatcherContext, WatcherEvent
from app.watchers.registry import get_watcher, register_watcher

logger = logging.getLogger(__name__)

WATCHER_NAME = "finra"

# The file for a day appears in the evening, US time. Every six hours catches it the
# same night or next morning, and a poll with nothing new costs no download.
POLL_INTERVAL_SECONDS = 6 * 60 * 60
COOLDOWN_SECONDS = 24 * 60 * 60
DAILY_FIRE_CAP = 1


def _default_symbols(context: WatcherContext) -> list[str]:
    from app.data_providers.universe import load_universe

    return [e.symbol for e in load_universe()[: context.settings.scan_universe_size]]


class FinraWatcher(Watcher):
    name = WATCHER_NAME
    description = (
        "FINRA daily short-volume files for the watchlist, downloaded once they are published. "
        "Stored quietly for the short-volume signal; it never alerts."
    )
    poll_interval_seconds = POLL_INTERVAL_SECONDS
    cooldown_seconds = COOLDOWN_SECONDS
    daily_fire_cap = DAILY_FIRE_CAP

    def __init__(
        self,
        *,
        provider: FinraProvider | None = None,
        symbols: Callable[[WatcherContext], list[str]] | None = None,
    ) -> None:
        self._provider = provider
        self._symbols = symbols or _default_symbols
        self.last_result = None

    def poll(self, context: WatcherContext) -> list[WatcherEvent]:
        symbols = self._symbols(context)
        if not symbols:
            return []
        result = refresh_finra_short_volume(
            context.session,
            symbols,
            self._provider or FinraProvider(),
            today=context.now.date(),
        )
        self.last_result = result
        for error in result.errors:
            logger.warning("finra watcher: %s", error)
        if result.errors and result.days_fetched == 0 and result.facts_existing == 0 and result.facts_created == 0:
            raise DataProviderError("FINRA short volume could not be downloaded: " + "; ".join(result.errors[:3]))
        return []


def register_finra_watcher() -> None:
    """Install the watcher once. Called at application start-up, not at import."""
    if get_watcher(WATCHER_NAME) is None:
        register_watcher(FinraWatcher())
