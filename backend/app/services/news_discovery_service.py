"""F2: find tickers outside the universe in this morning's headlines (PORTOFOLIO_BUILDER.md).

Pure fact extraction: a ticker is only taken from an explicit marker in a headline ("NASDAQ: XYZ", "$XYZ"),
never guessed or chosen by an AI, and only kept when the data provider returns a live quote for it. The
hits then go through the same quality gate and hold buy as any universe stock (run_hold_scan).
"""

from __future__ import annotations

import logging
import re

from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.cache import fresh_data_only
from app.data_providers.universe import get_default_watchlist, load_universe

logger = logging.getLogger(__name__)

# Exchange-prefixed ("NYSE: VST", "NASDAQ:VST") or a cashtag ("$VST"); plain capitalised words never match.
TICKER_MARKER = re.compile(r"(?:\b(?:NYSE|NASDAQ|AMEX|NYSEARCA|NYSEAMERICAN)\s*:\s*|\$)([A-Z]{1,5}(?:\.[A-Z])?)\b")


def tickers_in_headline(headline: str) -> list[str]:
    return list(dict.fromkeys(TICKER_MARKER.findall(headline)))


def discover_from_news(settings: AppSettings, data_provider: DataProvider) -> dict[str, str]:
    """{ticker: the headline that named it}, outside the universe, real, at most `news_discovery_max_per_run`."""
    cap = settings.news_discovery_max_per_run
    known = {e.symbol for e in load_universe()}
    found: dict[str, str] = {}
    rejected: set[str] = set()
    for source in get_default_watchlist(settings.scan_universe_size):
        if len(found) >= cap:
            break
        try:
            with fresh_data_only():
                items = data_provider.get_news(source, limit=20)
        except Exception:  # noqa: BLE001 - one source failing must not stop the rest
            logger.info("News discovery: no news for %s", source)
            continue
        for item in items:
            for ticker in tickers_in_headline(item.headline):
                if ticker in known or ticker in found or ticker in rejected or len(found) >= cap:
                    continue
                try:
                    with fresh_data_only():
                        if float(data_provider.get_quote(ticker).price) > 0:
                            found[ticker] = item.headline[:200]
                            continue
                except Exception:  # noqa: BLE001 - not a listed symbol we can price: skip, never guess
                    pass
                rejected.add(ticker)
    return found
