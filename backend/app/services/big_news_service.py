"""Big-news buy lane (rules only, no AI): a recent big headline or 8-K buys a stock WITHOUT the quality gate,
at `big_news_size_pct` of the normal slice. A recent negative hit, no live quote, or any engine refusal blocks it.
Runs only inside `automation_service.run_hold_scan`; never in the backtester."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from sqlmodel import Session

from app.analysis.big_news import BIG_8K_ITEMS, BigNewsHit, evaluate_cards, evaluate_headlines, parse_published
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.cache import fresh_data_only

logger = logging.getLogger(__name__)


MAX_MARKET_8K_PER_RUN = 20  # market-wide 8-K filers looked at per scan


def market_8k_symbols(session: Session, settings: AppSettings, now: datetime | None = None) -> list[str]:
    """Tickers (any, universe or not) with a stored 8-K carrying a big item inside the lookback window. The 8-Ks come
    from the SEC's market-wide latest-filings feed, which the smart-money poller reads and stores as dated facts."""
    from app.data_providers.sec_8k import filings_8k_as_of

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    days = max(1, math.ceil(settings.big_news_lookback_hours / 24))
    found = filings_8k_as_of(session, None, now, days, items=BIG_8K_ITEMS)  # type: ignore[arg-type]  # None = every symbol
    fresh = [f for f in found if f.symbol and (now - f.known_at).total_seconds() <= settings.big_news_lookback_hours * 3600]
    return list(dict.fromkeys(f.symbol.upper() for f in fresh))[:MAX_MARKET_8K_PER_RUN]


MAX_WIRE_PER_RUN = 30  # wire-feed tickers looked at per scan


WIRE_URLS: dict[str, str] = {}  # headline -> article url of the last wire fetch, so the news reader can link and read it


def wire_news_headlines(settings: AppSettings, now: datetime | None = None, only_big: bool = True) -> dict[str, str]:
    """{ticker: headline} for market-wide press releases (wire RSS feeds, plus a Google News search of the four big wires) that carry an exchange tag, sit inside
    the lookback window and match a big-news pattern with no negative one. Any ticker, universe or not. A feed that fails is
    skipped; `find_big_news` still re-checks each headline and the live quote is proved by `check_quality`."""
    from app.analysis.big_news import classify_headline, is_negative
    from app.data_providers.base import DataProviderError
    from app.data_providers.pr_newswire import FEED_URLS, fetch_feed_text, parse_feed, tickers_in_text

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    found: dict[str, str] = {}
    for url in FEED_URLS:
        try:
            releases = parse_feed(fetch_feed_text(url))
        except (DataProviderError, ValueError):
            logger.info("Big news: wire feed %s skipped", url)
            continue
        for r in releases:
            if r.published_at is None or (now - r.published_at).total_seconds() > settings.big_news_lookback_hours * 3600:
                continue
            if only_big and (classify_headline(r.title) is None or is_negative(r.title)):
                continue
            for ticker in tickers_in_text(f"{r.title} {r.summary}"):
                found.setdefault(ticker, r.title)
                WIRE_URLS[r.title] = r.url
    try:  # whole market: GlobeNewswire / Business Wire / PR Newswire / ACCESS Newswire releases via Google News RSS
        from app.data_providers.wire_search import wire_ticker_headlines

        for ticker, title in wire_ticker_headlines(settings.big_news_lookback_hours, now, WIRE_URLS).items():
            if not only_big or (classify_headline(title) is not None and not is_negative(title)):
                found.setdefault(ticker, title)
    except Exception:  # noqa: BLE001 - the SEC map or Google may be down; the other sources still count
        logger.info("Big news: whole-market wire search skipped", exc_info=True)
    return dict(list(found.items())[:MAX_WIRE_PER_RUN])


def find_big_news(
    settings: AppSettings, data_provider: DataProvider, session: Session, symbol: str, now: datetime | None = None,
    extra_headline: str | None = None,
) -> BigNewsHit | None:
    """The big-news trigger for `symbol` in the lookback window, or None (also None when a negative hit blocks it)."""
    from app.data_providers.sec_8k import filings_8k_as_of
    from app.watchers.sec_watcher import URGENT_8K_ITEMS

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    hours = settings.big_news_lookback_hours
    try:
        with fresh_data_only():
            news = data_provider.get_news(symbol, limit=20)
    except Exception:  # noqa: BLE001 - a symbol outside the universe may have no news feed; 8-Ks and the extra headline still count
        news = []
    items = [(n.headline, parse_published(n.published_at)) for n in news]
    if extra_headline:
        items.append((extra_headline, now))  # a market-wide headline found moments ago
    hit, blocked_by = evaluate_headlines(items, now, hours)
    if blocked_by:
        logger.info("Big news: %s blocked by negative headline: %s", symbol, blocked_by)
        return None
    days = max(1, math.ceil(hours / 24))
    filings = [f for f in filings_8k_as_of(session, symbol, now, days) if (now - f.known_at).total_seconds() <= hours * 3600]
    if any(URGENT_8K_ITEMS.intersection(f.items) for f in filings):
        logger.info("Big news: %s blocked by an urgent 8-K item", symbol)
        return None
    try:
        from app.knowledge.news_cards import cards_for_symbol

        card_hit, card_block = evaluate_cards([dict(f.payload) for f in cards_for_symbol(session, symbol)], now, hours)
    except Exception:  # noqa: BLE001 - unreadable cards are no evidence either way
        logger.exception("Big news: could not read the news cards of %s", symbol)
        card_hit, card_block = None, None
    if card_block:
        logger.info("Big news: %s blocked by AI-read negative news: %s", symbol, card_block)
        return None
    hit = hit or card_hit
    if hit is None:
        for f in filings:
            items = set(BIG_8K_ITEMS).intersection(f.items)
            if items:
                return BigNewsHit("material 8-K", f"8-K item {', '.join(sorted(items))} filed {f.known_at:%Y-%m-%d %H:%M} UTC")
    return hit


def big_news_scan_symbol(
    settings: AppSettings, data_provider: DataProvider, session: Session, engine, symbol: str, extra_headline: str | None = None,
) -> bool:
    """True = bought. A hit is analysed (gate result stored, alert sent, stock added to the universe) whether or not
    it buys: 100% of a slice if it also passes the gate, `big_news_size_pct` if not. Engine refusals are logged and
    mean "not bought"; no live quote, MarketClosedError and the unexpected propagate."""
    from app.portfolio.engine import (
        DuplicatePositionError, InsufficientCashError, MaxPositionsExceededError, SectorConcentrationError,
        SleeveDisabledError, StalePlanError,
    )
    from app.services.hold_buy_service import check_quality, hold_buy
    from app.services.market_find_service import LANE_BIG_NEWS, adopt_found_stock

    hit = find_big_news(settings, data_provider, session, symbol, extra_headline=extra_headline)
    if hit is None:
        return False
    gate = check_quality(data_provider, symbol, session=session)  # also proves a live quote exists
    adopt_found_stock(session, settings, data_provider, symbol, LANE_BIG_NEWS, f'{hit.category}: "{hit.headline[:200]}"', gate)
    if settings.big_news_skip_if_up_pct > 0:  # analysed and added above; only the buy is skipped
        with fresh_data_only():
            move = float(data_provider.get_quote(symbol).change_pct_24h)
        if move >= settings.big_news_skip_if_up_pct:
            logger.info("Big news: %s not bought: already up %.1f%% today (skip at %.1f%%)", symbol, move, settings.big_news_skip_if_up_pct)
            return False
    try:
        hold_buy(session, engine, data_provider, settings, symbol, size_pct=100.0 if gate.passed else settings.big_news_size_pct,
                 reason=f'hold scan [big news lane] ({hit.category}, headline: "{hit.headline[:200]}"): '
                 + ("passed the quality gate" if gate.passed else "failed the quality gate (" + "; ".join(gate.failed_reasons) + "), reduced size"))
        return True
    except (InsufficientCashError, DuplicatePositionError, MaxPositionsExceededError, SectorConcentrationError,
            SleeveDisabledError, StalePlanError) as exc:
        logger.info("Big news: %s not bought: %s", symbol, exc)
        return False
