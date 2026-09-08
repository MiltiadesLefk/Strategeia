from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.universe import get_default_watchlist
from app.llm_providers.base import LLMProvider
from app.portfolio.models import PaperPosition
from app.services.scanner_service import scan_symbols
from app.services.telegram_service import notify
from app.services.trade_plan_service import generate_trade_plan

logger = logging.getLogger(__name__)


def run_auto_scan(
    settings: AppSettings,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    session: Session,
) -> list[str]:
    """Closes the full automation loop: scan the bundled universe, and for
    each 'potential_setup' symbol that doesn't already have an open
    position, generate a trade plan (which itself auto-executes into a
    paper position if settings.auto_execute_trade_plans is on).

    Guardrail: never pushes open positions past max_concurrent_positions —
    without a cap, an unattended scan finding N setups every tick would
    open N new positions every tick with no limit. Highest-scoring
    candidates are proposed first when there are more setups than slots.
    """
    open_symbols = {p.symbol for p in session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all()}
    slots = settings.max_concurrent_positions - len(open_symbols)
    if slots <= 0:
        return []

    symbols = get_default_watchlist(settings.scan_universe_size)
    results, _errors = scan_symbols(symbols, data_provider)
    candidates = sorted(
        (r for r in results if r.signal == "potential_setup" and r.symbol not in open_symbols),
        key=lambda r: r.score,
        reverse=True,
    )

    generated: list[str] = []
    for candidate in candidates[:slots]:
        try:
            generate_trade_plan(
                candidate.symbol, settings.paper_starting_cash, settings.default_risk_pct, data_provider, llm_provider, session
            )
            generated.append(candidate.symbol)
        except Exception:
            logger.exception("Auto-scan: failed to generate a trade plan for %s", candidate.symbol)

    if generated:
        notify(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            f"Auto-scan generated {len(generated)} new trade plan(s): {', '.join(generated)}",
        )
    return generated
