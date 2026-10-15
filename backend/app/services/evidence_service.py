"""Reads the facts for one stock (as of current_as_of(), through the knowledge readers) and turns them into an
EvidenceBonus. Any failed read counts as no evidence, never a penalty. Live scan only; the backtest does not run it."""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.analysis.big_news import evaluate_headlines, parse_published
from app.analysis.evidence_bonus import EvidenceBonus, compute_bonus
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.cache import fresh_data_only
from app.knowledge import current_as_of
from app.knowledge.congress_trades import congress_trades_as_of
from app.knowledge.insider_trades import insider_activity_as_of, insider_clusters_as_of

logger = logging.getLogger(__name__)


def evidence_for(settings: AppSettings, data_provider: DataProvider, session: Session, symbol: str) -> EvidenceBonus:
    now = current_as_of()
    insider_buys, cluster, congress_buys, big, negative = 0, False, 0, False, False
    try:
        activity = insider_activity_as_of(session, symbol, now)
        insider_buys = activity.buy_count if activity else 0
        cluster = bool(insider_clusters_as_of(session, symbol, now))
        congress_buys = len(congress_trades_as_of(session, symbol, now, side="buy"))
    except Exception:  # noqa: BLE001 - unreadable facts are no evidence
        logger.exception("Evidence: could not read smart-money facts for %s", symbol)
    try:
        with fresh_data_only():
            news = data_provider.get_news(symbol, limit=20)
        hit, blocked = evaluate_headlines([(n.headline, parse_published(n.published_at)) for n in news], now, settings.big_news_lookback_hours)
        big, negative = hit is not None, blocked is not None
    except Exception:  # noqa: BLE001 - no news feed is no evidence
        pass
    return compute_bonus(insider_buys, cluster, congress_buys, big, negative)
