"""Big-news buy lane (rules only, no AI): a recent big headline or 8-K buys a stock WITHOUT the quality gate,
at `big_news_size_pct` of the normal slice. A recent negative hit, no live quote, or any engine refusal blocks it.
Runs only inside `automation_service.run_hold_scan`; never in the backtester."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from sqlmodel import Session

from app.analysis.big_news import BIG_8K_ITEMS, BigNewsHit, evaluate_headlines, parse_published
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
    try:
        hold_buy(session, engine, data_provider, settings, symbol, size_pct=100.0 if gate.passed else settings.big_news_size_pct,
                 reason=f'hold scan [big news lane] ({hit.category}, headline: "{hit.headline[:200]}"): '
                 + ("passed the quality gate" if gate.passed else "failed the quality gate (" + "; ".join(gate.failed_reasons) + "), reduced size"))
        return True
    except (InsufficientCashError, DuplicatePositionError, MaxPositionsExceededError, SectorConcentrationError,
            SleeveDisabledError, StalePlanError) as exc:
        logger.info("Big news: %s not bought: %s", symbol, exc)
        return False
