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


def _report(session: Session, run_id: str | None, row: dict, outcome: str) -> None:
    """List one analysed smart-money stock under the day's report on the Ratings page (best effort)."""
    if not run_id:
        return
    try:
        from app.services.rating_service import record_report_item

        record_report_item(session, run_id, "scan", row["symbol"], "smart money trades", f"Score {row['total']:.1f}: {outcome}",
                           [{"text": r, "meta": "", "url": ""} for r in row.get("reasons", [])[:8]])
    except Exception:  # noqa: BLE001
        logger.info("Could not list the smart-money stock %s in the report", row.get("symbol"), exc_info=True)


def adopt_smart_money_finds(
    settings: AppSettings, data_provider: DataProvider, session: Session, run_id: str | None = None, rate: bool = False,
) -> list[str]:
    """The symbols newly added to the universe, best score first, at most `smart_money_adopt_max_per_run`.
    With `run_id`, every top-ranked stock looked at is listed in that day's report with what happened to it.
    With `rate` (Rate all) the stock gets the full rating, is saved on the Ratings page, and joins only if it reaches the bar."""
    if settings.smart_money_adopt_max_per_run <= 0 or is_simulated():
        return []
    from app.services.hold_buy_service import check_quality

    added: list[str] = []
    for n, row in enumerate(ranked_stocks(session, settings, current_as_of())):
        if n >= 30:
            run_id = None  # the report lists the top 30 only
            if len(added) >= settings.smart_money_adopt_max_per_run:
                break
        symbol = row["symbol"]
        if len(added) >= settings.smart_money_adopt_max_per_run:
            _report(session, run_id, row, "not looked at (the per-run limit of new stocks was reached)")
            continue
        if row["total"] < settings.smart_money_adopt_min_score or in_saved_universe(symbol):
            _report(session, run_id, row, "already in the universe" if in_saved_universe(symbol) else "score under the minimum to add")
            continue  # ranked best first, but a sell-heavy or already-known stock is skipped, not a stop
        try:
            if rate:
                from app.services.rating_service import rate_stock

                gate = rate_stock(data_provider, symbol, settings.min_confidence_for_trade, session)
            else:
                gate = check_quality(data_provider, symbol, session=session)
        except Exception:  # noqa: BLE001 - no live quote or price history: not analysable, so not adopted
            logger.info("Smart-money find %s skipped: no usable data", symbol, exc_info=True)
            _report(session, run_id, row, "skipped, no live quote or price history")
            continue
        detail = f"smart-money score {row['total']:.1f} ({row['reasons'][0] if row['reasons'] else 'buy reported'})"
        if rate and run_id:
            from app.services.rating_service import record_rating

            record_rating(session, run_id, "scan", symbol, LANE_SMART_MONEY, gate, note=gate.checks[0].note,
                          found=f"Smart money: {row['reasons'][0] if row['reasons'] else 'buy reported'}")
        if rate and not gate.passed:
            continue  # rated and listed above; under the bar, so it does not join
        if adopt_found_stock(session, settings, data_provider, symbol, LANE_SMART_MONEY, detail, gate):
            added.append(symbol)
            continue  # adopt_found_stock already listed it as added to the universe
        _report(session, run_id, row, "analysed, not added")
    return added
