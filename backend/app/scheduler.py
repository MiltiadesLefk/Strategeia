from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session

from app.config import load_app_settings
from app.data_providers.factory import get_data_provider
from app.database import engine
from app.portfolio.engine import PaperTradingEngine

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _mark_to_market_job() -> None:
    settings = load_app_settings()
    data_provider = get_data_provider(settings)
    with Session(engine) as session:
        try:
            PaperTradingEngine(session, data_provider, settings.paper_starting_cash).mark_to_market()
        except Exception:
            logger.exception("Scheduled mark-to-market tick failed")


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
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
