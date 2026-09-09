from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.universe import get_default_watchlist
from app.llm_providers.base import LLMProvider
from app.portfolio.models import PaperPosition
from app.services.telegram_service import notify
from app.services.trade_plan_service import generate_trade_plan

logger = logging.getLogger(__name__)


@dataclass
class AutoScanOutcome:
    generated: list[str] = field(default_factory=list)
    no_trade: list[str] = field(default_factory=list)


def run_auto_scan(
    settings: AppSettings,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    session: Session,
) -> AutoScanOutcome:
    """Closes the full automation loop: every symbol in the bundled universe
    that doesn't already have an open position gets a *full* evaluation
    (price/volume/technicals/fundamentals/news/earnings, via
    generate_trade_plan) every run — quality over quantity, so the bot never
    silently skips a name, it either proposes a plan or explicitly logs why
    not (see TradePlanRecord.status == "no_trade").

    Guardrail: never AUTO-EXECUTES past max_concurrent_positions — without a
    cap, an unattended scan finding N setups every tick would open N new
    positions every tick with no limit. Evaluation itself is never capped
    though: once the cap is reached, remaining symbols still get a real plan
    (left "pending" for manual review) instead of not being looked at.
    """
    open_symbols = {p.symbol for p in session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all()}
    slots_remaining = max(0, settings.max_concurrent_positions - len(open_symbols))

    symbols = [s for s in get_default_watchlist(settings.scan_universe_size) if s not in open_symbols]

    outcome = AutoScanOutcome()
    for symbol in symbols:
        try:
            response = generate_trade_plan(
                symbol,
                settings.paper_starting_cash,
                settings.default_risk_pct,
                data_provider,
                llm_provider,
                session,
                allow_auto_execute=slots_remaining > 0,
            )
        except Exception:
            logger.exception("Auto-scan: failed to evaluate %s", symbol)
            continue

        if response.direction is None:
            outcome.no_trade.append(symbol)
            continue

        outcome.generated.append(symbol)
        if response.status == "executed":
            slots_remaining -= 1

    if outcome.generated or outcome.no_trade:
        message = (
            f"Auto-scan evaluated {len(symbols)} symbol(s): {len(outcome.generated)} trade plan(s)"
            + (f" ({', '.join(outcome.generated)})" if outcome.generated else "")
            + f", {len(outcome.no_trade)} no-trade."
        )
        notify(settings.telegram_bot_token, settings.telegram_chat_id, message)
    return outcome
