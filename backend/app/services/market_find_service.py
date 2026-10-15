"""What happens to every stock a whole-market lane (big news, smart money) finds, bought or not (APP_LOGIC.md):
the quality gate result and its failed reasons are stored as a dated fact, an alert says what was found and whether it
passed, and the stock joins the saved universe for good. Idempotent; does nothing under a simulated moment."""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.analysis.quality_gate import GateResult
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.knowledge import FactKind, record_fact
from app.knowledge.point_in_time import is_simulated
from app.services.telegram_service import notify

logger = logging.getLogger(__name__)

LANE_BIG_NEWS = "big news"
LANE_SMART_MONEY = "smart money"


def adopt_found_stock(
    session: Session, settings: AppSettings, data_provider: DataProvider, symbol: str, lane: str, detail: str, gate: GateResult | None,
) -> bool:
    """Store the analysis, alert, add to the universe. Best effort: never raises. True = newly added to the universe."""
    if is_simulated():
        return False
    from app.services.watchlist_service import add_to_universe
    from app.timeutil import utcnow_naive

    now = utcnow_naive()
    passed = gate.passed if gate is not None else None
    failed = list(gate.failed_reasons) if gate is not None and not gate.passed else []
    try:
        record_fact(
            session, kind=FactKind.WATCHER_EVENT, source="market_find", symbol=symbol,
            dedupe_key=f"market_find:{lane}:{symbol}:{now:%Y-%m-%d}",
            payload={"lane": lane, "detail": detail, "gate_passed": passed, "failed_reasons": failed},
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not store the market find for %s", symbol)
        session.rollback()
    added = False
    try:
        added = add_to_universe(symbol, data_provider)
    except Exception:  # noqa: BLE001
        logger.exception("Could not add %s to the universe", symbol)
    verdict = "no quality check" if gate is None else "passed the quality gate" if passed else "failed the quality gate (" + "; ".join(failed) + ")"
    notify(settings.telegram_bot_token, settings.telegram_chat_id,
           f"{lane} found {symbol}: {detail}. It {verdict}." + (" Added to the universe." if added else ""))
    return added
