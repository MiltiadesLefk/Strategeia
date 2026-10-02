"""The morning note: one Telegram brief on each trading day, built from the app's own data.

What goes in (every part is allowed to fail on its own and is then listed as unavailable):
overnight futures, VIX and index levels; open paper positions with their result and distance
to stop and first target; plans waiting for you or for the open; the best-scoring setups on
the watchlist; today's scheduled events (macro releases, earnings of held and top-setup
names); and what the watchers found in the last day (alerts, insider-buying clusters).

The text is rule-based and always complete. An optional AI paragraph that restates the facts
can be added (see note_common.with_ai_paragraph); it never supplies a number. Reading only:
nothing here opens, changes or closes a position.

The overall shape follows Anthropic's "morning-note" skill (lead with what matters, then
developments, then the day's events; "nothing material" is a valid note): see
llm_providers/morning_note_prompt.py.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from app.config import AppSettings
from app.data_providers import universe
from app.data_providers.base import DataProvider
from app.data_providers.cache import fresh_data_only
from app.knowledge import FactKind, facts_known_as_of
from app.llm_providers.base import LLMProvider
from app.llm_providers.morning_note_prompt import build_morning_note_prompt
from app.markets import format_market_time, to_market_time
from app.portfolio.models import DeferredEvaluation, PaperPosition, TradePlanRecord
from app.schemas.calendar_schemas import CalendarResponse
from app.schemas.scan_schemas import ScanResultSchema
from app.services.calendar_service import build_calendar
from app.services.note_common import (
    NoteFacts,
    NoteResult,
    Section,
    capped,
    fmt_money,
    position_r_multiple,
    render_rule_based,
    safely,
    short,
    with_ai_paragraph,
)
from app.services.notification_schedule import market_day_text, morning_note_due
from app.services.notification_text import SendOutcome, Sender, last_sent_day, mark_sent, retry_pause_active, send_text, telegram_configured
from app.services.scanner_service import scan_symbols
from app.services.telegram_service import send_message
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

NOTIFICATION_KEY = "morning_note"

# Overnight context, as the data providers spell them. Whatever has no fresh quote is
# listed as unavailable; nothing is filled in.
OVERNIGHT_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("ES=F", "S&P 500 futures"),
    ("NQ=F", "Nasdaq 100 futures"),
    ("^VIX", "VIX"),
    ("SPY", "SPY"),
)
# The watchlist scan reads this many symbols at most (the provider's 15-minute cache means
# most are already loaded by the day's earlier scans), and the note lists the best few.
MORNING_NOTE_SCAN_LIMIT = 40
TOP_SETUPS_LISTED = 5
# Plans still waiting from the last days, and watcher events from the last day.
PENDING_PLAN_LOOKBACK = timedelta(days=3)
EVENT_LOOKBACK = timedelta(hours=24)
CLUSTER_LOOKBACK = timedelta(days=7)
# Today and tomorrow: a morning note that stops at midnight would miss an 8:30 release
# tomorrow that matters for today's positions.
CALENDAR_DAYS_AHEAD = 1
INSIDER_CLUSTER_KIND = "insider_cluster"

CalendarBuilder = Callable[..., CalendarResponse]
ScanRunner = Callable[[list[str], DataProvider], tuple[list[ScanResultSchema], list[str]]]


def _quote(provider: DataProvider, symbol: str):
    return provider.get_quote(symbol)


def _market_section(provider: DataProvider, unavailable: list[str]) -> Section:
    section = Section("Overnight and market")
    missing: list[str] = []
    with fresh_data_only():
        for symbol, label in OVERNIGHT_SYMBOLS:
            try:
                quote = _quote(provider, symbol)
            except Exception:  # noqa: BLE001 - one missing level must not drop the others
                missing.append(label)
                continue
            section.lines.append(f"{label}: {quote.price:,.2f} ({quote.change_pct_24h:+.2f}%)")
    if missing:
        unavailable.append("no fresh quote for " + ", ".join(missing))
    return section


def _position_lines(session: Session, provider: DataProvider, unavailable: list[str]) -> tuple[Section, list[PaperPosition]]:
    positions = list(session.exec(select(PaperPosition).where(PaperPosition.status == "open").order_by(PaperPosition.opened_at)).all())
    section = Section(f"Open positions ({len(positions)})")
    lines: list[str] = []
    no_price: list[str] = []
    with fresh_data_only():
        for position in positions:
            thesis = getattr(position, "thesis_status", None)  # filled in by the thesis feature when present
            head = f"{position.symbol} {position.direction} {position.shares} sh from {position.entry_price:,.2f}"
            levels = f"stop {position.stop_loss:,.2f}, first target {position.tp1:,.2f}"
            try:
                price = float(_quote(provider, position.symbol).price)
            except Exception:  # noqa: BLE001
                no_price.append(position.symbol)
                lines.append(f"{head}; price not available; {levels}")
                continue
            sign = 1 if position.direction == "long" else -1
            pnl = (price - position.entry_price) * sign * position.shares
            pnl_pct = (price / position.entry_price - 1) * 100 * sign
            r_value = position_r_multiple(position.direction, position.entry_price, position.stop_loss, price)
            stop_gap = (price - position.stop_loss) * sign / price * 100
            tp_gap = (position.tp1 - price) * sign / price * 100
            parts = [
                f"{head}, now {price:,.2f} ({pnl_pct:+.1f}%, {fmt_money(pnl)}"
                + (f", {r_value:+.1f}R" if r_value is not None else "")
                + ")",
                f"{stop_gap:.1f}% to stop {position.stop_loss:,.2f}" if stop_gap > 0 else f"at or past stop {position.stop_loss:,.2f}",
                f"{tp_gap:.1f}% to first target {position.tp1:,.2f}" if tp_gap > 0 else f"at or past first target {position.tp1:,.2f}",
            ]
            if thesis:
                parts.append(f"thesis: {short(str(thesis), 60)}")
            lines.append("; ".join(parts))
    if no_price:
        unavailable.append("no fresh price for " + ", ".join(no_price))
    section.lines, section.more = capped(lines)
    return section, positions


def _pending_section(session: Session, now: datetime) -> Section:
    plans = session.exec(
        select(TradePlanRecord)
        .where(TradePlanRecord.status == "pending", TradePlanRecord.created_at >= now - PENDING_PLAN_LOOKBACK)
        .order_by(TradePlanRecord.created_at.desc())
    ).all()
    redos = session.exec(select(DeferredEvaluation).where(DeferredEvaluation.status == "pending")).all()
    lines = [
        f"{p.symbol} {p.direction or ''} plan, confidence {p.confidence_score}%"
        + (f", entry {p.entry:,.2f}, stop {p.stop:,.2f}, first target {p.tp1:,.2f}" if p.entry and p.stop and p.tp1 else "")
        + " (waiting for you)"
        for p in plans
    ]
    lines += [f"{r.symbol}: to be re-evaluated after the open ({short(r.reason, 60)})" for r in redos]
    section = Section("Plans waiting")
    section.lines, section.more = capped(lines)
    return section


def _setups_section(
    settings: AppSettings, provider: DataProvider, held: set[str], scan_runner: ScanRunner
) -> tuple[Section, list[str]]:
    symbols = universe.get_default_watchlist(min(settings.scan_universe_size, MORNING_NOTE_SCAN_LIMIT))
    results, _errors = scan_runner(symbols, provider)
    ranked = sorted(
        (r for r in results if r.signal == "potential_setup" and r.direction and r.symbol not in held),
        key=lambda r: (-r.score, r.symbol),
    )[:TOP_SETUPS_LISTED]
    section = Section("Watchlist: top setups (rule-based scan)")
    section.lines = [
        f"{r.symbol} {r.direction}, score {r.score}/6, trend {r.trend}, momentum {r.momentum}, last {r.price:,.2f} ({r.change_pct_24h:+.1f}%)"
        for r in ranked
    ]
    return section, [r.symbol for r in ranked]


def _calendar_section(
    session: Session,
    provider: DataProvider,
    today: date,
    interesting_symbols: set[str],
    calendar_builder: CalendarBuilder,
    unavailable: list[str],
) -> Section:
    calendar = calendar_builder(
        session,
        provider,
        from_date=today,
        to_date=today + timedelta(days=CALENDAR_DAYS_AHEAD),
        symbols=sorted(interesting_symbols),
    )
    for source in calendar.sources:
        if source.status == "unavailable":
            unavailable.append(source.label.lower())
    lines: list[str] = []
    for item in calendar.items:
        if item.kind == "earnings" and item.symbol not in interesting_symbols:
            continue
        if item.kind == "economic" and item.impact not in ("High", None):
            continue
        when = "today" if item.days_until == 0 else "tomorrow"
        at = f" {item.time_et} ET" if item.time_et else ""
        lines.append(f"{when}{at}: {short(item.title, 80)}" + (f" (forecast {item.forecast}, previous {item.previous})" if item.forecast else ""))
    section = Section("Scheduled today and tomorrow")
    section.lines, section.more = capped(lines)
    return section


def _event_line(symbol: str | None, headline: str) -> str:
    """"AAA: headline", without repeating the symbol when the headline already starts with it."""
    who = symbol or "market"
    text = short(headline, 140)
    return text if text.startswith(f"{who}:") else f"{who}: {text}"


def _watcher_section(session: Session, now: datetime) -> Section:
    events = facts_known_as_of(session, FactKind.WATCHER_EVENT, since=now - EVENT_LOOKBACK, limit=40)
    lines: list[str] = []
    for fact in events:
        payload = fact.payload or {}
        if payload.get("suppressed_reason") or payload.get("kind") == INSIDER_CLUSTER_KIND:
            continue
        lines.append(_event_line(fact.symbol, payload.get("headline", "")))
    section = Section("Watcher events, last 24 hours")
    section.lines, section.more = capped(lines)
    return section


def _cluster_section(session: Session, now: datetime) -> Section:
    events = facts_known_as_of(session, FactKind.WATCHER_EVENT, since=now - CLUSTER_LOOKBACK, limit=100)
    lines = [
        _event_line(fact.symbol, (fact.payload or {}).get("headline", ""))
        for fact in events
        if (fact.payload or {}).get("kind") == INSIDER_CLUSTER_KIND
    ]
    section = Section("Insider-buying clusters, last 7 days")
    section.lines, section.more = capped(lines)
    return section


def gather_morning_facts(
    session: Session,
    settings: AppSettings,
    data_provider: DataProvider,
    now: datetime | None = None,
    *,
    calendar_builder: CalendarBuilder = build_calendar,
    scan_runner: ScanRunner = scan_symbols,
) -> NoteFacts:
    now = now if now is not None else utcnow_naive()
    now_et = to_market_time(now)
    facts = NoteFacts(heading=f"Strategeia morning note - {format_market_time(now_et)}")
    unavailable = facts.unavailable

    market = safely("overnight and market levels", unavailable, lambda: _market_section(data_provider, unavailable), Section("Overnight and market"))
    positions_section, positions = safely(
        "open positions", unavailable, lambda: _position_lines(session, data_provider, unavailable), (Section("Open positions"), [])
    )
    held = {p.symbol for p in positions}
    pending = safely("pending plans", unavailable, lambda: _pending_section(session, now), Section("Plans waiting"))
    setups, setup_symbols = safely(
        "watchlist setups", unavailable, lambda: _setups_section(settings, data_provider, held, scan_runner), (Section("Watchlist"), [])
    )
    calendar = safely(
        "calendar",
        unavailable,
        lambda: _calendar_section(session, data_provider, now_et.date(), held | set(setup_symbols), calendar_builder, unavailable),
        Section("Scheduled today and tomorrow"),
    )
    events = safely("watcher events", unavailable, lambda: _watcher_section(session, now), Section("Watcher events"))
    clusters = safely("insider clusters", unavailable, lambda: _cluster_section(session, now), Section("Insider clusters"))

    facts.sections = [market, positions_section, pending, calendar, setups, events, clusters]
    if not any(s.lines for s in facts.sections[1:]):
        facts.sections.insert(1, Section("Summary", ["Nothing material to report: no open positions, waiting plans, scheduled events or alerts."]))
    return facts


def generate_morning_note(
    session: Session,
    settings: AppSettings,
    data_provider: DataProvider,
    llm_provider: LLMProvider | None = None,
    now: datetime | None = None,
    *,
    use_ai: bool = False,
    calendar_builder: CalendarBuilder = build_calendar,
    scan_runner: ScanRunner = scan_symbols,
) -> NoteResult:
    """The note's text. `use_ai` is the caller's choice (the preview button leaves it off
    unless asked, so previewing never spends an AI call by surprise)."""
    now = now if now is not None else utcnow_naive()
    facts = gather_morning_facts(
        session, settings, data_provider, now, calendar_builder=calendar_builder, scan_runner=scan_runner
    )
    result = with_ai_paragraph(
        render_rule_based(facts), facts, llm_provider, use_ai=use_ai, build_prompt=build_morning_note_prompt
    )
    result.generated_at = now
    return result


def run_morning_note_if_due(
    session: Session,
    settings: AppSettings,
    data_provider: DataProvider,
    llm_provider: LLMProvider | None,
    now: datetime | None = None,
    *,
    sender: Sender = send_message,
    calendar_builder: CalendarBuilder = build_calendar,
    scan_runner: ScanRunner = scan_symbols,
) -> str:
    """What the scheduler tick calls. Returns what happened: "not_due", "no_telegram",
    "sent" or "failed". Nothing is built or spent unless the note is due and there is
    somewhere to send it; the day is marked only after a successful send, so a failed send
    is tried again on the next tick (within the catch-up window)."""
    now = now if now is not None else utcnow_naive()
    if not morning_note_due(
        now,
        enabled=settings.morning_note_enabled,
        time_et=settings.morning_note_time_et,
        last_sent_day=last_sent_day(session, NOTIFICATION_KEY),
    ):
        return "not_due"
    if not telegram_configured(settings):
        return "no_telegram"
    if retry_pause_active(session, NOTIFICATION_KEY, now):
        return "not_due"
    note = generate_morning_note(
        session, settings, data_provider, llm_provider, now, use_ai=True, calendar_builder=calendar_builder, scan_runner=scan_runner
    )
    outcome: SendOutcome = send_text(settings, note.text, sender)
    if outcome.ok:
        mark_sent(session, NOTIFICATION_KEY, now)
        return "sent"
    mark_sent(session, NOTIFICATION_KEY, now, error=outcome.message)
    return "failed"


__all__ = [
    "generate_morning_note",
    "gather_morning_facts",
    "market_day_text",
    "run_morning_note_if_due",
]
