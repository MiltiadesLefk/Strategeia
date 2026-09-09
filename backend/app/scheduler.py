from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session

from app.config import load_app_settings
from app.data_providers.factory import get_data_provider
from app.database import engine
from app.llm_providers.factory import get_llm_provider
from app.portfolio.engine import PaperTradingEngine
from app.services.automation_service import run_auto_scan
from app.services.health_monitor import check_and_alert

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None

# Auto-scan runs 3x/day pegged to session opens rather than a fixed
# interval — "quality over quantity": evaluate every ticker once per
# session, not every N minutes. UTC hours are approximate fixed points (not
# DST-adjusted — Asia/Tokyo doesn't observe DST so 00:00 UTC is exact;
# London/New York drift by an hour across DST, which is fine for a
# 3x/day cadence, not worth the added complexity of a tz-aware scheduler
# entry for a personal dashboard).
AUTO_SCAN_SESSION_TIMES_UTC = [
    ("asia", 0, 0),  # Tokyo open, ~09:00 JST
    ("london", 8, 0),  # London open, ~08:00 GMT
    ("new_york", 13, 30),  # NYSE open, ~09:30 ET
]


def _mark_to_market_job() -> None:
    settings = load_app_settings()
    data_provider = get_data_provider(settings)
    with Session(engine) as session:
        try:
            PaperTradingEngine(session, data_provider, settings.paper_starting_cash).mark_to_market()
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
    for session_name, hour, minute in AUTO_SCAN_SESSION_TIMES_UTC:
        _scheduler.add_job(
            _auto_scan_job,
            CronTrigger(hour=hour, minute=minute, timezone="UTC"),
            id=f"auto_scan_{session_name}",
        )
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
