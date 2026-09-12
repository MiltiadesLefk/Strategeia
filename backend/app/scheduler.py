from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from app.config import load_app_settings
from app.data_providers.factory import get_data_provider
from app.database import engine
from app.llm_providers.factory import get_llm_provider
from app.markets import US_MARKET_TZ, is_always_on, is_us_market_open, is_within_post_close_grace
from app.portfolio.engine import PaperTradingEngine
from app.portfolio.models import PaperPosition
from app.services.automation_service import run_auto_scan
from app.services.health_monitor import check_and_alert

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None

# Auto-scan runs 3x/day, anchored to the session the tradeable universe
# actually trades in. It used to fire at the Tokyo and London opens against a
# US-equity watchlist: two of the three daily scans evaluated (and could
# auto-execute) US stocks on the previous session's close, since no new US
# bar exists at 00:00 or 08:00 UTC.
#
# Expressed in market-local time with a tz-aware trigger rather than fixed
# UTC hours, so APScheduler tracks DST instead of the whole schedule sliding
# an hour twice a year. Weekdays only — the US session is the anchor.
AUTO_SCAN_SESSION_TIMES_ET = [
    ("open", 10, 0),  # 30 min after the bell: the opening range has formed
    ("midday", 13, 0),  # mid-session
    ("post_close", 16, 15),  # after the close, once the day's bar is final
]


def _should_mark_now(session: Session) -> bool:
    """Skip ticks that cannot possibly find anything new: outside the US
    session nothing an equity position depends on moves, so polling every 15
    minutes through the night just burns provider quota (and, on yfinance,
    rate limit that the next real scan needs). Crypto positions never stop
    moving, so the presence of one keeps marking on around the clock."""
    if is_us_market_open() or is_within_post_close_grace():
        return True
    open_symbols = session.exec(select(PaperPosition.symbol).where(PaperPosition.status == "open")).all()
    return any(is_always_on(symbol) for symbol in open_symbols)


def _mark_to_market_job() -> None:
    settings = load_app_settings()
    data_provider = get_data_provider(settings)
    with Session(engine) as session:
        try:
            if not _should_mark_now(session):
                return
            PaperTradingEngine(
                session,
                data_provider,
                settings.paper_starting_cash,
                settings.max_concurrent_positions,
                slippage_bps=settings.slippage_bps,
                commission_per_trade=settings.commission_per_trade,
                max_positions_per_sector=settings.max_positions_per_sector,
                max_position_pct_of_adv=settings.max_position_pct_of_adv,
            ).mark_to_market()
        except Exception:
            logger.exception("Scheduled mark-to-market tick failed")


def _health_check_job() -> None:
    settings = load_app_settings()
    data_provider = get_data_provider(settings)
    try:
        check_and_alert(settings, data_provider)
    except Exception:
        logger.exception("Scheduled health-check tick failed")


def _auto_scan_job() -> None:
    settings = load_app_settings()
    if not settings.auto_scan_enabled:
        return
    data_provider = get_data_provider(settings)
    llm_provider = get_llm_provider(settings)
    with Session(engine) as session:
        try:
            run_auto_scan(settings, data_provider, llm_provider, session)
        except Exception:
            logger.exception("Scheduled auto-scan tick failed")


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    settings = load_app_settings()
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _mark_to_market_job,
        "interval",
        minutes=settings.mark_to_market_interval_minutes,
        id="mark_to_market",
    )
    _scheduler.add_job(
        _health_check_job,
        "interval",
        minutes=settings.mark_to_market_interval_minutes,
        id="health_check",
        next_run_time=datetime.now(),  # catch a broken provider right at startup, not just after the first interval
    )
    for session_name, hour, minute in AUTO_SCAN_SESSION_TIMES_ET:
        _scheduler.add_job(
            _auto_scan_job,
            CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=US_MARKET_TZ),
            id=f"auto_scan_{session_name}",
        )
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
