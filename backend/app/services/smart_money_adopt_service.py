"""Smart-money finds join the universe: a stock whose smart-money BUY score reaches `smart_money_adopt_min_score` is
analysed (the quality gate, which also proves a live quote), alerted and added to the saved universe for good, bought or
not. Buying is not decided here: the smart-money pipeline keeps its own, higher minimum. Rules only, no AI.
Runs inside the morning hold scan; never in a backtest (`adopt_found_stock` does nothing under a simulated moment)."""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.knowledge import current_as_of
from app.knowledge.point_in_time import is_simulated
from app.services.market_find_service import LANE_SMART_MONEY, adopt_found_stock
from app.services.smart_money_overview_service import ranked_stocks
from app.services.watchlist_service import in_saved_universe

logger = logging.getLogger(__name__)


def adopt_smart_money_finds(settings: AppSettings, data_provider: DataProvider, session: Session) -> list[str]:
    """The symbols newly added to the universe, best score first, at most `smart_money_adopt_max_per_run`."""
    if settings.smart_money_adopt_max_per_run <= 0 or is_simulated():
        return []
    from app.services.hold_buy_service import check_quality

    added: list[str] = []
    for row in ranked_stocks(session, settings, current_as_of()):
        if len(added) >= settings.smart_money_adopt_max_per_run:
            break
        symbol = row["symbol"]
        if row["total"] < settings.smart_money_adopt_min_score or in_saved_universe(symbol):
            continue  # ranked best first, but a sell-heavy or already-known stock is skipped, not a stop
        try:
            gate = check_quality(data_provider, symbol, session=session)
        except Exception:  # noqa: BLE001 - no live quote or price history: not analysable, so not adopted
            logger.info("Smart-money find %s skipped: no usable data", symbol, exc_info=True)
            continue
        detail = f"smart-money score {row['total']:.1f} ({row['reasons'][0] if row['reasons'] else 'buy reported'})"
        if adopt_found_stock(session, settings, data_provider, symbol, LANE_SMART_MONEY, detail, gate):
            added.append(symbol)
    return added
