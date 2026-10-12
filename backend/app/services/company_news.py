"""The headlines a plan or research page reads for one company.

Google News is searched by the company's name and exchange ticker first: those stories are about the company.
If that gives fewer than `limit`, the configured data providers (Yahoo, Finnhub when enabled, ...) top it up,
keeping only headlines that name the company or its ticker, because their per-ticker feeds are baskets that
include peers and market roundups. Never fabricates: with no source answering the list is empty.

Not used inside a simulated (backtest) moment, where the data provider is the only source and shows only what
was knowable then. `GOOGLE_NEWS_ENABLED=false` switches the Google search off (the tests do).
"""

from __future__ import annotations

import logging
import os
import re

from app.data_providers.base import AllProvidersFailedError, DataProvider, DataProviderError, NewsItem
from app.data_providers.google_news import GoogleNewsProvider
from app.knowledge import is_simulated
from app.services.news_card_service import name_terms, names_company

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 5
DAYS = 7
TOP_UP_FETCH = 20

_google = GoogleNewsProvider()


def google_news_enabled() -> bool:
    return os.environ.get("GOOGLE_NEWS_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def build_query(symbol: str, company_name: str | None) -> str | None:
    """`"Vistra" OR "NYSE:VST" OR "NASDAQ:VST"`, or None for a symbol with nothing to search by (crypto pairs)."""
    ticker = symbol.strip().upper()
    if "-" in ticker or not re.fullmatch(r"[A-Z0-9.]{1,6}", ticker):
        return None
    terms = name_terms(company_name)
    parts = [f'"{terms[0]}"'] if terms else []
    parts += [f'"NYSE:{ticker}"', f'"NASDAQ:{ticker}"']
    return " OR ".join(parts)


def _key(item: NewsItem) -> str:
    return re.sub(r"[^a-z0-9]+", " ", item.headline.lower()).strip()[:80]


def fetch_company_news(
    symbol: str, company_name: str | None, data_provider: DataProvider, limit: int = DEFAULT_LIMIT
) -> list[NewsItem]:
    if is_simulated() or not google_news_enabled():
        return data_provider.get_news(symbol, limit=limit)

    items: list[NewsItem] = []
    query = build_query(symbol, company_name)
    if query:
        try:
            found = list(_google.search(query, limit=limit * 2, days=DAYS))
            seen_keys: set[str] = set()
            for found_item in found:
                if _key(found_item) not in seen_keys:
                    seen_keys.add(_key(found_item))
                    items.append(found_item)
        except DataProviderError as exc:
            logger.warning("google news unavailable for %s: %s", symbol, exc)
    if len(items) >= limit:
        return items[:limit]

    try:
        more = data_provider.get_news(symbol, limit=TOP_UP_FETCH)
    except (AllProvidersFailedError, DataProviderError):
        if items:
            return items
        raise
    seen = {_key(i) for i in items}
    for item in more:
        if len(items) >= limit:
            break
        if _key(item) in seen:
            continue
        if company_name and not names_company(item.headline, symbol, company_name):
            continue
        seen.add(_key(item))
        items.append(item)
    return items
