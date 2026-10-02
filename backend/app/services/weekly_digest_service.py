"""The weekly digest: a Telegram review sent on the last trading day of the week.

It sums up the week from the app's own records: trades closed (in R), the equity change,
plans taken versus not taken (and what the ones not taken would have earned, from the
missed-trades report), the calibration headline with its own warning about small samples,
the lessons written, how many watcher events came in, and next week's calendar.

Same build as the morning note: rule-based text that is always complete, plus an optional
labelled AI paragraph that only restates the facts. Reading only: nothing here changes a
position or a setting. Every number is computed from stored records; with too few trades
the digest says so instead of reading anything into them.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.knowledge import FactKind, facts_known_as_of
from app.llm_providers.base import LLMProvider
from app.llm_providers.morning_note_prompt import build_weekly_digest_prompt
from app.markets import format_market_time, to_market_time
from app.portfolio.calibration import MIN_TRADES_FOR_READING, compute_calibration
from app.portfolio.missed_trade_report import STATE_RESOLVED, build_missed_trade_report
from app.portfolio.models import EquitySnapshot, PaperPosition, TradePlanRecord
from app.services.calendar_service import build_calendar
from app.services.morning_note_service import CalendarBuilder
from app.services.note_common import NoteFacts, NoteResult, Section, capped, fmt_money, render_rule_based, safely, short, with_ai_paragraph
from app.services.notification_schedule import weekly_digest_due, week_start_utc
from app.services.notification_text import (
    SendOutcome,
    Sender,
    last_sent_day,
    mark_sent,
    retry_pause_active,
    send_text,
    telegram_configured,
)
from app.services.telegram_service import send_message
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

NOTIFICATION_KEY = "weekly_digest"
# Closed trades are listed best first and worst last, this many of each at most.
TRADES_LISTED = 8
# Lesson authors are named by symbol only: the lesson text itself stays in the app.
LESSON_SYMBOLS_LISTED = 10


def _week_trades(session: Session, week_start: datetime) -> list[PaperPosition]:
    return list(
        session.exec(
            select(PaperPosition)
            .where(PaperPosition.status == "closed", PaperPosition.closed_at >= week_start)
            .order_by(PaperPosition.closed_at)
        ).all()
    )


def _trades_section(trades: list[PaperPosition]) -> Section:
    section = Section(f"Trades closed this week ({len(trades)})")
    if not trades:
        return section
    with_r = [t for t in trades if t.realized_r is not None]
    wins = sum(1 for t in trades if (t.realized_pnl or 0) > 0)
    total_pnl = sum(t.realized_pnl or 0 for t in trades)
    total_r = sum(t.realized_r for t in with_r)
    section.lines.append(
        f"{wins} won, {len(trades) - wins} did not; total {fmt_money(total_pnl)}"
        + (f" and {total_r:+.1f}R over {len(with_r)} trades with a recorded R" if with_r else "")
    )
    ranked = sorted(with_r, key=lambda t: t.realized_r, reverse=True)
    if ranked:
        best, worst = ranked[0], ranked[-1]
        section.lines.append(f"Best: {best.symbol} {best.direction} {best.realized_r:+.1f}R ({best.close_reason or 'closed'})")
        if worst is not best:
            section.lines.append(f"Worst: {worst.symbol} {worst.direction} {worst.realized_r:+.1f}R ({worst.close_reason or 'closed'})")
    for trade in trades[:TRADES_LISTED]:
        r_text = f"{trade.realized_r:+.1f}R" if trade.realized_r is not None else "R not recorded"
        section.lines.append(f"{trade.symbol} {trade.direction}: {r_text}, {fmt_money(trade.realized_pnl or 0)}, {trade.close_reason or 'closed'}")
    if len(trades) < MIN_TRADES_FOR_READING:
        section.lines.append(
            f"Only {len(trades)} closed trade{'s' if len(trades) != 1 else ''} this week: far too few to read anything into."
        )
    return section


def _equity_section(session: Session, week_start: datetime, now: datetime) -> Section:
    latest = session.exec(select(EquitySnapshot).where(EquitySnapshot.timestamp <= now).order_by(EquitySnapshot.timestamp.desc())).first()
    before = session.exec(
        select(EquitySnapshot).where(EquitySnapshot.timestamp < week_start).order_by(EquitySnapshot.timestamp.desc())
    ).first()
    if before is None:
        before = session.exec(
            select(EquitySnapshot).where(EquitySnapshot.timestamp >= week_start).order_by(EquitySnapshot.timestamp)
        ).first()
    section = Section("Equity")
    if latest is None or before is None or before.equity_value <= 0:
        raise ValueError("no equity snapshots")
    change = latest.equity_value - before.equity_value
    section.lines.append(
        f"{before.equity_value:,.0f} to {latest.equity_value:,.0f} ({fmt_money(change)}, {change / before.equity_value * 100:+.2f}%)"
        + ("" if before.timestamp < week_start else " since the first snapshot this week")
    )
    return section


def _plans_section(session: Session, week_start: datetime) -> Section:
    plans = session.exec(select(TradePlanRecord).where(TradePlanRecord.created_at >= week_start)).all()
    counts: dict[str, int] = {}
    for plan in plans:
        counts[plan.status] = counts.get(plan.status, 0) + 1
    section = Section("Plans this week")
    section.lines.append(
        f"{len(plans)} evaluations: {counts.get('executed', 0)} taken, "
        f"{counts.get('pending', 0) + counts.get('discarded', 0)} tradeable but not taken, "
        f"{counts.get('no_trade', 0)} no-trade decisions"
    )
    try:
        report = build_missed_trade_report(session)
    except Exception:  # noqa: BLE001 - the report is a bonus; the counts above stand alone
        logger.warning("Weekly digest: missed-trades report unavailable")
        return section
    resolved = [t for t in report.trades if t.created_at >= week_start and t.state == STATE_RESOLVED and t.r_multiple is not None]
    if resolved:
        total = sum(t.r_multiple for t in resolved)
        section.lines.append(
            f"Of the plans not taken this week, {len(resolved)} already have a final hypothetical result: {total:+.1f}R in all "
            "(idealised fills, not real results)"
        )
    return section


def _calibration_section(session: Session) -> Section:
    report = compute_calibration(session)
    section = Section("Calibration")
    section.lines.append(short(report.headline, 400))
    return section


def _lessons_section(trades: list[PaperPosition]) -> Section:
    written = [t for t in trades if t.lesson_text]
    section = Section(f"Lessons written this week ({len(written)})")
    if written:
        symbols = list(dict.fromkeys(t.symbol for t in written))[:LESSON_SYMBOLS_LISTED]
        section.lines.append("For: " + ", ".join(symbols) + " (the text is on the Portfolio page)")
    return section


def _events_section(session: Session, week_start: datetime) -> Section:
    events = facts_known_as_of(session, FactKind.WATCHER_EVENT, since=week_start)
    fired = [e for e in events if not (e.payload or {}).get("suppressed_reason")]
    section = Section("Watchers")
    if events:
        section.lines.append(f"{len(fired)} watcher event{'s' if len(fired) != 1 else ''} this week ({len(events) - len(fired)} more held back by cooldowns)")
    return section


def _next_week_section(
    session: Session, provider: DataProvider, now: datetime, calendar_builder: CalendarBuilder, unavailable: list[str]
) -> Section:
    today = to_market_time(now).date()
    next_monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    held = {p.symbol for p in session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all()}
    calendar = calendar_builder(session, provider, from_date=next_monday, to_date=next_monday + timedelta(days=4), symbols=sorted(held))
    for source in calendar.sources:
        if source.status == "unavailable":
            unavailable.append(source.label.lower())
    lines: list[str] = []
    for item in calendar.items:
        if item.kind == "earnings" and item.symbol not in held:
            continue
        if item.kind == "economic" and item.impact not in ("High", None):
            continue
        lines.append(f"{_weekday(item.date)}{' ' + item.time_et + ' ET' if item.time_et else ''}: {short(item.title, 80)}")
    section = Section("Next week")
    section.lines, section.more = capped(lines)
    return section


def _weekday(iso_day: str) -> str:
    return date.fromisoformat(iso_day).strftime("%a %d %b")


def gather_digest_facts(
    session: Session,
    settings: AppSettings,
    data_provider: DataProvider,
    now: datetime | None = None,
    *,
    calendar_builder: CalendarBuilder = build_calendar,
) -> NoteFacts:
    now = now if now is not None else utcnow_naive()
    week_start = week_start_utc(now)
    facts = NoteFacts(heading=f"Strategeia weekly digest - week to {format_market_time(to_market_time(now))}")
    unavailable = facts.unavailable
    trades = safely("closed trades", unavailable, lambda: _week_trades(session, week_start), [])
    facts.sections = [
        _trades_section(trades),
        safely("equity change", unavailable, lambda: _equity_section(session, week_start, now), Section("Equity")),
        safely("plans", unavailable, lambda: _plans_section(session, week_start), Section("Plans this week")),
        safely("calibration", unavailable, lambda: _calibration_section(session), Section("Calibration")),
        _lessons_section(trades),
        safely("watcher events", unavailable, lambda: _events_section(session, week_start), Section("Watchers")),
        safely(
            "next week's calendar",
            unavailable,
            lambda: _next_week_section(session, data_provider, now, calendar_builder, unavailable),
            Section("Next week"),
        ),
    ]
    if not trades:
        facts.sections[0].lines.append("No trades closed this week.")
    return facts


def generate_weekly_digest(
    session: Session,
    settings: AppSettings,
    data_provider: DataProvider,
    llm_provider: LLMProvider | None = None,
    now: datetime | None = None,
    *,
    use_ai: bool = False,
    calendar_builder: CalendarBuilder = build_calendar,
) -> NoteResult:
    now = now if now is not None else utcnow_naive()
    facts = gather_digest_facts(session, settings, data_provider, now, calendar_builder=calendar_builder)
    result = with_ai_paragraph(
        render_rule_based(facts), facts, llm_provider, use_ai=use_ai, build_prompt=build_weekly_digest_prompt
    )
    result.generated_at = now
    return result


def run_weekly_digest_if_due(
    session: Session,
    settings: AppSettings,
    data_provider: DataProvider,
    llm_provider: LLMProvider | None,
    now: datetime | None = None,
    *,
    sender: Sender = send_message,
    calendar_builder: CalendarBuilder = build_calendar,
) -> str:
    """Scheduler entry point; same contract as run_morning_note_if_due."""
    now = now if now is not None else utcnow_naive()
    if not weekly_digest_due(now, enabled=settings.weekly_digest_enabled, last_sent_day=last_sent_day(session, NOTIFICATION_KEY)):
        return "not_due"
    if not telegram_configured(settings):
        return "no_telegram"
    if retry_pause_active(session, NOTIFICATION_KEY, now):
        return "not_due"
    note = generate_weekly_digest(session, settings, data_provider, llm_provider, now, use_ai=True, calendar_builder=calendar_builder)
    outcome: SendOutcome = send_text(settings, note.text, sender)
    if outcome.ok:
        mark_sent(session, NOTIFICATION_KEY, now)
        return "sent"
    mark_sent(session, NOTIFICATION_KEY, now, error=outcome.message)
    return "failed"
