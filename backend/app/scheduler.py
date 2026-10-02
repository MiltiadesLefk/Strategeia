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
from app.markets import (
    US_MARKET_TZ,
    is_always_on,
    is_us_market_open,
    is_us_trading_day,
    is_within_post_close_grace,
    to_market_time,
    us_holiday_name,
)
from app.knowledge.point_in_time import is_simulated
from app.services.sleeve_service import mark_all_sleeves
from app.portfolio.missed_trades import refresh_missed_trades
from app.portfolio.models import PaperPosition
from app.services.automation_service import run_auto_scan, run_market_open_redos
from app.services.health_monitor import check_and_alert
from app.services.morning_note_service import run_morning_note_if_due
from app.services.price_alert_service import run_price_alert_check
from app.services.weekly_digest_service import run_weekly_digest_if_due
from app.services.lesson_service import is_real_llm, run_lesson_catchup
from app.services.thesis_service import run_thesis_sweep
from app.watchers.runner import run_due_watchers

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

# The market-open redo (D10 = C) is polled on this cadence through the US
# session, weekdays; run_market_open_redos itself waits for 09:45 ET (the
# open + REDO_DELAY_AFTER_OPEN) and skips holidays and closed hours. Polling
# rather than one 09:45 run means a redo missed while the app was down, or
# one whose data fetch failed, is picked up on a later tick the same day.
MARKET_OPEN_REDO_CRON = {"day_of_week": "mon-fri", "hour": "9-15", "minute": "0,15,30,45"}


def _should_mark_now(session: Session) -> bool:
    """Skip ticks that cannot possibly find anything new: outside the US
    session (weekends, holidays, and after a 1:00 pm early close included)
    nothing an equity position depends on moves, so polling every 15 minutes
    through the night just burns provider quota (and, on yfinance, rate limit
    that the next real scan needs). Crypto positions never stop moving, so the
    presence of one keeps marking on around the clock."""
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
            # Every sleeve (each through its own engine), disabled ones included:
            # switching a sleeve off stops new trades, not the exits of open ones.
            mark_all_sleeves(session, data_provider, settings)
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
    today = to_market_time().date()
    if not is_us_trading_day(today):
        # The cron only knows weekdays. On a market holiday there is no new
        # bar to evaluate, and every plan it made could only be queued for
        # the next open, which evaluates it again anyway.
        logger.info("Auto-scan skipped: US market closed for %s", us_holiday_name(today))
        return
    data_provider = get_data_provider(settings)
    llm_provider = get_llm_provider(settings)
    with Session(engine) as session:
        try:
            run_auto_scan(settings, data_provider, llm_provider, session)
        except Exception:
            logger.exception("Scheduled auto-scan tick failed")


def _market_open_redo_job() -> None:
    if not is_us_market_open():
        return  # cheap exit before building providers: holidays, early closes
    settings = load_app_settings()
    data_provider = get_data_provider(settings)
    llm_provider = get_llm_provider(settings)
    with Session(engine) as session:
        try:
            run_market_open_redos(settings, data_provider, llm_provider, session)
        except Exception:
            logger.exception("Scheduled market-open redo tick failed")


# Lessons for closed trades are written by their own job, never by the exit sweep:
# a slow or failing AI call can therefore never delay or break closing a position.
# Every few minutes it finds recently closed positions with no lesson and writes a
# few (see services/lesson_service.run_lesson_catchup); with no real AI provider
# configured it returns before doing any work.
LESSON_CATCHUP_INTERVAL_MINUTES = 5


def _lesson_catchup_job() -> None:
    try:
        settings = load_app_settings()
        llm_provider = get_llm_provider(settings)
        if not is_real_llm(llm_provider):
            return  # cheap exit before building the data provider or opening the database
        data_provider = get_data_provider(settings)
        with Session(engine) as session:
            run_lesson_catchup(session, data_provider, llm_provider)
    except Exception:
        logger.exception("Scheduled lesson catch-up tick failed")


# The missed-trades ledger is recomputed once a day, after the close, when the day's
# bars are final (see portfolio/missed_trades.py). Analysis only: it places nothing and
# decides nothing, and it is skipped inside a simulated (backtest) moment.
MISSED_TRADES_REFRESH_TIME_ET = (16, 45)


def _missed_trades_refresh_job() -> None:
    try:
        if is_simulated():
            return
        settings = load_app_settings()
        with Session(engine) as session:
            refresh_missed_trades(session, settings)
    except Exception:
        logger.exception("Scheduled missed-trades refresh failed")


# Watchers: one tick wakes every watchers_poll_minutes and runs whichever watchers
# are due (each has its own slower interval). The tick is jittered so it doesn't land
# on the same second as the other jobs, and each watcher's poll is spread by up to
# WATCHER_POLL_JITTER_SECONDS. A no-op while the master switch is off.
WATCHER_TICK_JITTER_SECONDS = 20
WATCHER_POLL_JITTER_SECONDS = 5.0


# Thesis tracker: every few minutes each open position without a thesis gets one made from its
# plan, and (in the trading session only, when a new bar can exist) every thesis not checked in
# the last hour is re-checked by rules. It never touches the position itself, and a failure here
# can never delay the exit sweep because it is a separate job.
THESIS_SWEEP_INTERVAL_MINUTES = 15


def _thesis_sweep_job() -> None:
    try:
        settings = load_app_settings()
        data_provider = get_data_provider(settings)
        with Session(engine) as session:
            run_thesis_sweep(session, settings, data_provider, recheck=_should_mark_now(session))
    except Exception:
        logger.exception("Scheduled thesis sweep failed")


# Price alerts (user-defined and the automatic ones on open positions) are checked on
# this cadence; the check itself skips symbols whose market is closed, so a tick outside
# the session costs one database read. It only sends messages and records facts.
PRICE_ALERT_INTERVAL_MINUTES = 5


def _price_alerts_job() -> None:
    try:
        if is_simulated():
            return
        settings = load_app_settings()
        data_provider = get_data_provider(settings)
        with Session(engine) as session:
            run_price_alert_check(session, settings, data_provider)
    except Exception:
        logger.exception("Scheduled price alert check failed")


# The morning note and the weekly digest are polled every few minutes rather than fired by a
# fixed cron time: the time is a setting that can change while the app runs, and a note
# missed because the app was down at 08:45 is still sent when it starts at 08:50. The
# due-check (services/notification_schedule.py) decides, and a sent record prevents repeats.
NOTIFICATION_POLL_MINUTES = 5


def _notifications_job() -> None:
    try:
        if is_simulated():
            return
        settings = load_app_settings()
        if not (settings.morning_note_enabled or settings.weekly_digest_enabled):
            return  # cheap exit before building providers or opening the database
        data_provider = get_data_provider(settings)
        llm_provider = get_llm_provider(settings)
        with Session(engine) as session:
            run_morning_note_if_due(session, settings, data_provider, llm_provider)
            run_weekly_digest_if_due(session, settings, data_provider, llm_provider)
    except Exception:
        logger.exception("Scheduled notification tick failed")


def _watchers_job() -> None:
    try:
        settings = load_app_settings()
        if not settings.watchers_enabled:
            return  # cheap exit before opening the database
        with Session(engine) as session:
            run_due_watchers(session, settings, jitter_seconds=WATCHER_POLL_JITTER_SECONDS)
    except Exception:
        logger.exception("Scheduled watchers tick failed")


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
    _scheduler.add_job(
        _market_open_redo_job,
        CronTrigger(**MARKET_OPEN_REDO_CRON, timezone=US_MARKET_TZ),
        id="market_open_redo",
    )
    _scheduler.add_job(
        _missed_trades_refresh_job,
        CronTrigger(
            day_of_week="mon-fri",
            hour=MISSED_TRADES_REFRESH_TIME_ET[0],
            minute=MISSED_TRADES_REFRESH_TIME_ET[1],
            timezone=US_MARKET_TZ,
        ),
        id="missed_trades_refresh",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _lesson_catchup_job,
        "interval",
        minutes=LESSON_CATCHUP_INTERVAL_MINUTES,
        id="lesson_catchup",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _watchers_job,
        "interval",
        minutes=settings.watchers_poll_minutes,
        jitter=WATCHER_TICK_JITTER_SECONDS,
        id="watchers",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _price_alerts_job,
        "interval",
        minutes=PRICE_ALERT_INTERVAL_MINUTES,
        id="price_alerts",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _notifications_job,
        "interval",
        minutes=NOTIFICATION_POLL_MINUTES,
        id="notifications",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _thesis_sweep_job,
        "interval",
        minutes=THESIS_SWEEP_INTERVAL_MINUTES,
        id="thesis_sweep",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
