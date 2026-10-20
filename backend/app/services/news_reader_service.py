"""The morning news reader: the AI reads the headlines before the hold scan decides anything.

For every stock the scan will rate (and, with the big-news lane on, for market-wide wire headlines) it saves the
latest headlines, has the AI label each new one once (a news card: event, sentiment, materiality, certainty, and for
positive or negative ones the article body), then stops. What the labels MEAN is decided by plain rules elsewhere:
`rating_service` (up to +-3 points) and `analysis/big_news.evaluate_cards` (the big-news lane). The AI never picks
or sizes a buy. Everything is bounded per scan, best-effort, and does nothing without a usable AI or inside a backtest.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlmodel import Session

from app.config import AppSettings
from app.data_providers.base import DataProvider, NewsItem
from app.knowledge import is_simulated
from app.llm_providers.base import LLMProvider
from app.services.archive_service import archive_news
from app.services.news_card_service import MAX_LLM_CALLS_PER_RUN, label_new_items, llm_is_usable

logger = logging.getLogger(__name__)

SOURCE = "news_reader"
MAX_LLM_CALLS_PER_SCAN = 30  # headline-labelling calls in one scan (about 10 headlines each)
MAX_ARTICLE_READS_PER_SCAN = 12  # article bodies read in one scan (one more AI call each)
HEADLINES_PER_SYMBOL = 20


def read_news(
    settings: AppSettings, data_provider: DataProvider, llm_provider: LLMProvider | None, session: Session,
    symbols: list[str], *, market: bool = False,
) -> dict[str, str]:
    """Label the new headlines of `symbols`; with `market`, also those of market-wide wire releases. Returns
    {ticker: headline} for the market-wide tickers read (not in `symbols`), the big-news lane's extra candidates."""
    if is_simulated() or not settings.news_cards_enabled or not llm_is_usable(llm_provider):
        return {}
    calls = articles = 0

    def label(symbol: str, name: str | None) -> None:
        nonlocal calls, articles
        done = label_new_items(
            session, symbol, llm_provider, settings.news_card_batch_limit, company_name=name,
            max_calls=min(MAX_LLM_CALLS_PER_RUN, MAX_LLM_CALLS_PER_SCAN - calls),
            max_articles=MAX_ARTICLE_READS_PER_SCAN - articles,
        )
        calls += done.llm_calls
        articles += done.articles_read

    for symbol in symbols:
        if calls >= MAX_LLM_CALLS_PER_SCAN:
            logger.info("News reader: the AI-call budget is spent; the rest keep keyword news")
            break
        try:
            archive_news(session, symbol, data_provider.get_news(symbol, limit=HEADLINES_PER_SYMBOL), SOURCE)
            try:
                name = data_provider.get_company_overview(symbol).name
            except Exception:  # noqa: BLE001 - the name only filters out other companies' headlines
                name = None
            label(symbol, name)
        except Exception:  # noqa: BLE001 - one stock's news failing never stops the scan
            logger.info("News reader: %s skipped", symbol, exc_info=True)

    found: dict[str, str] = {}
    if market:
        from app.services.big_news_service import WIRE_URLS, wire_news_headlines

        try:
            wire = wire_news_headlines(settings, only_big=False)
        except Exception:  # noqa: BLE001
            logger.info("News reader: market-wide wire headlines unavailable", exc_info=True)
            wire = {}
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for ticker, title in wire.items():
            if ticker in symbols or calls >= MAX_LLM_CALLS_PER_SCAN:
                continue
            try:
                archive_news(session, ticker, [NewsItem(headline=title, source="wire", url=WIRE_URLS.get(title, ""), published_at=now)], SOURCE)
                label(ticker, None)
                found[ticker] = title
            except Exception:  # noqa: BLE001
                logger.info("News reader: wire headline for %s skipped", ticker, exc_info=True)
    return found
