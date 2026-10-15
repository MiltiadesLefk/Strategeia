from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

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
from app.services import smart_money_event_review_service, smart_money_pipeline_service
from app.services.sleeve_service import check_touch_stops_all_sleeves, mark_all_sleeves
from app.timeutil import utcnow_naive
from app.portfolio.models import PaperPosition
from app.services.automation_service import run_hold_scan, run_market_open_redos
from app.services.health_monitor import check_and_alert
from app.services.morning_note_service import run_morning_note_if_due
from app.services.price_alert_service import run_price_alert_check
from app.services.weekly_digest_service import run_weekly_digest_if_due
from app.services.kill_switch_service import evaluate_kill_switches
from app.services.telegram_service import notify as telegram_notify
from app.services.lesson_service import is_real_llm, run_lesson_catchup
from app.services.thesis_service import run_thesis_sweep
from app.services.sell_plan_service import run_hold_reviews, run_sell_plan
from app.services.insider_coverage_service import run_insider_coverage_tick
from app.watchers.runner import run_due_watchers, run_morning_fetch

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
    ("open", 10, 0),  # 30 min after the bell: the opening range has formed (one daily scan, PORTOFOLIO_BUILDER D6)
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
    with Session(engine) as session:
        try:
            run_hold_scan(settings, data_provider, session)
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


def _touch_stop_job() -> None:
    try:
        if is_simulated():
            return
        settings = load_app_settings()
        if settings.hold_stop_mode != "touch":
            return
        with Session(engine) as session:
            check_touch_stops_all_sleeves(session, get_data_provider(settings), settings)
    except Exception:
        logger.exception("Scheduled touch-stop check failed")


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


# How often the kill switches look at the stored equity curve and trade results. The check
# reads the database only (no market data), and does nothing while the feature is off.
KILL_SWITCH_INTERVAL_MINUTES = 10


def _kill_switch_job() -> None:
    try:
        if is_simulated():
            return
        settings = load_app_settings()
        if not settings.kill_switch_enabled:
            return
        with Session(engine) as session:
            evaluate_kill_switches(
                session,
                settings,
                lambda text: telegram_notify(settings.telegram_bot_token, settings.telegram_chat_id, text),
            )
    except Exception:
        logger.exception("Scheduled kill switch check failed")


def _watchers_job() -> None:
    try:
        settings = load_app_settings()
        if not settings.watchers_enabled:
            return  # cheap exit before opening the database
        with Session(engine) as session:
            run_due_watchers(session, settings, jitter_seconds=WATCHER_POLL_JITTER_SECONDS)
    except Exception:
        logger.exception("Scheduled watchers tick failed")


# Hold mode (PORTOFOLIO_BUILDER D7): the smart-money sources are pulled once, before the open, and the
# candidates are evaluated just after the 09:45 redo. Registered at startup, like the other intervals.
MORNING_FETCH_TIME_ET = (9, 27)
HOLD_PIPELINE_TIME_ET = (9, 50)


def _morning_fetch_job() -> None:
    try:
        settings = load_app_settings()
        if not settings.watchers_enabled or not is_us_trading_day(to_market_time().date()):
            return
        with Session(engine) as session:
            run_morning_fetch(session, settings)
    except Exception:
        logger.exception("Morning smart-money fetch failed")


INSIDER_COVERAGE_INTERVAL_MINUTES = 10


_insider_coverage_lock = threading.Lock()


def _insider_coverage_job() -> None:
    # Runs whether or not the Watchers switch is on: a year of insider history must always be stored for every
    # symbol in the universe (the pacing is the shared SEC rate limiter plus the tick budget).
    if not _insider_coverage_lock.acquire(blocking=False):
        return  # a tick is already running (the scheduled one, or one started by a watchlist save)
    try:
        if is_simulated():
            return
        from app.data_providers.universe import load_universe

        with Session(engine) as session:
            run_insider_coverage_tick(session, [e.symbol for e in load_universe()])
    except Exception:
        logger.exception("Insider coverage tick failed")
    finally:
        _insider_coverage_lock.release()


def kick_insider_coverage() -> None:
    """Start a coverage tick now in the background (after the watchlist changed), instead of waiting for the next one."""
    threading.Thread(target=_insider_coverage_job, name="insider-coverage-kick", daemon=True).start()


# Smart-money pipeline: every few minutes the candidates the tracker flagged are evaluated (liquidity floor,
# the ordinary full evaluation, routing to auto-buy or the review queue), review items past their expiry are
# closed, and held smart-money positions get their event-exit review (an alert, never a sale). A tick with
# nothing to do is a couple of reads. Evaluations wait for the open, so nothing here fills at a stale price.
SMART_MONEY_EVENT_REVIEW_INTERVAL_MINUTES = 30


def _smart_money_pipeline_job() -> None:
    try:
        if is_simulated():
            return
        settings = load_app_settings()
        data_provider = get_data_provider(settings)
        llm_provider = get_llm_provider(settings)
        with Session(engine) as session:
            smart_money_pipeline_service.expire_review_items(session, utcnow_naive())
            smart_money_pipeline_service.run_pipeline(session, settings, data_provider, llm_provider)
    except Exception:
        logger.exception("Scheduled smart-money pipeline tick failed")


def _smart_money_event_review_job() -> None:
    try:
        settings = load_app_settings()
        data_provider = get_data_provider(settings)
        with Session(engine) as session:
            smart_money_event_review_service.run_event_review(session, settings, data_provider)
    except Exception:
        logger.exception("Scheduled smart-money event review failed")


# Sell plan + rebuy watch (F4): once a day shortly after the scan, on fresh data, in the session
# (selling and rebuying open and close positions at a real price).
SELL_PLAN_TIME_ET = (10, 30)


def _sell_plan_job() -> None:
    try:
        if is_simulated() or not is_us_trading_day(to_market_time().date()):
            return
        settings = load_app_settings()
        data_provider = get_data_provider(settings)
        with Session(engine) as session:
            run_sell_plan(settings, data_provider, session)
            run_hold_reviews(settings, data_provider, session)
    except Exception:
        logger.exception("Scheduled sell plan failed")


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
        _sell_plan_job,
        CronTrigger(day_of_week="mon-fri", hour=SELL_PLAN_TIME_ET[0], minute=SELL_PLAN_TIME_ET[1], timezone=US_MARKET_TZ),
        id="sell_plan",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _market_open_redo_job,
        CronTrigger(**MARKET_OPEN_REDO_CRON, timezone=US_MARKET_TZ),
        id="market_open_redo",
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
        _insider_coverage_job,
        "interval",
        minutes=INSIDER_COVERAGE_INTERVAL_MINUTES,
        id="insider_coverage",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),  # first fill right after startup
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
        _touch_stop_job,
        "interval",
        minutes=PRICE_ALERT_INTERVAL_MINUTES,
        id="touch_stop",
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
    _scheduler.add_job(
        _morning_fetch_job,
        CronTrigger(day_of_week="mon-fri", hour=MORNING_FETCH_TIME_ET[0], minute=MORNING_FETCH_TIME_ET[1], timezone=US_MARKET_TZ),
        id="morning_fetch", max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        _smart_money_pipeline_job,
        CronTrigger(day_of_week="mon-fri", hour=HOLD_PIPELINE_TIME_ET[0], minute=HOLD_PIPELINE_TIME_ET[1], timezone=US_MARKET_TZ),
        id="smart_money_pipeline",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _smart_money_event_review_job,
        "interval",
        minutes=SMART_MONEY_EVENT_REVIEW_INTERVAL_MINUTES,
        id="smart_money_event_review",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _kill_switch_job,
        "interval",
        minutes=KILL_SWITCH_INTERVAL_MINUTES,
        id="kill_switch",
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
