"""One timeline of dated events: scheduled economic releases, the Fed/CPI/jobs-report
dates we keep by hand, and company earnings, with the ones that touch your open
paper positions flagged.

Three sources, each allowed to fail on its own (the page says which one is missing):

- the live economic-calendar feed (`data_providers/econ_calendar.py`): this week only,
  with forecast and previous values;
- the built-in table (`analysis/macro_calendar.py`): Fed decisions, CPI, jobs report,
  further out than the feed reaches;
- earnings dates from the data provider for the watchlist plus any open positions.

Read-only: it only reads positions and cached data, never writes.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone

from sqlmodel import Session, select

from app.analysis import macro_calendar
from app.data_providers import universe
from app.data_providers.base import DataProvider
from app.data_providers.econ_calendar import (
    FEED_LABEL,
    EconCalendarResult,
    EconEvent,
    compare_with_macro_table,
    get_econ_calendar,
    macro_series_for_title,
)
from app.markets import US_MARKET_TZ
from app.portfolio.models import PaperPosition
from app.schemas.calendar_schemas import (
    CalendarItem,
    CalendarResponse,
    CatalystEntry,
    MacroMismatchSchema,
    PositionCatalysts,
    SourceStatus,
)
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

# The widest span one request may ask for (about two months: enough for a month view
# plus the days around it) and the default span when none is given.
MAX_RANGE_DAYS = 62
DEFAULT_RANGE_DAYS = 14
# How far ahead a market-wide event counts as something an open position will sit
# through, and how far ahead a position's own catalysts are listed.
POSITION_HORIZON_DAYS = 14
# Earnings rows come from one provider call per symbol (each cached for a day), so
# the default watchlist set is bounded; held symbols are always added on top.
MAX_EARNINGS_SYMBOLS = 60
EARNINGS_WORKERS = 8
# More than this share of symbols failing makes the earnings source "partial".
EARNINGS_PARTIAL_FAILURE_SHARE = 0.2

TABLE_LABEL = "Built-in Fed / CPI / jobs-report dates"
PROVIDER_LABEL = "Market data provider"
# Release times are fixed by the agencies: the Fed statement at 2:00 pm Eastern, the
# BLS reports at 8:30 am. The table itself holds dates only.
MACRO_TIMES_ET: dict[str, time] = {
    "FOMC decision": time(14, 0),
    "CPI release": time(8, 30),
    "jobs report": time(8, 30),
}
HIGH_IMPACT = "High"


class CalendarRangeError(ValueError):
    """The requested date range is invalid; the message is shown to the caller."""


def _today_et(now_utc: datetime | None) -> date:
    moment = now_utc or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(US_MARKET_TZ).date()


def _starts_at_utc(day: date, at: time) -> str:
    local = datetime.combine(day, at, tzinfo=US_MARKET_TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def _economic_item(event: EconEvent, today: date, in_table: bool | None) -> CalendarItem:
    return CalendarItem(
        id=f"econ:{event.starts_at.isoformat()}:{event.title}",
        kind="economic",
        title=event.title,
        date=event.date_et.isoformat(),
        time_et=event.time_et,
        starts_at=event.starts_at.isoformat() + "Z",
        days_until=(event.date_et - today).days,
        impact=event.impact,
        forecast=event.forecast,
        previous=event.previous,
        actual=event.actual,
        source="feed",
        source_label=FEED_LABEL,
        confirmed_by_feed=in_table,
    )


def _macro_items(
    start: date, end: date, today: date, feed_series_days: set[tuple[str, date]], econ: EconCalendarResult
) -> list[CalendarItem]:
    items = []
    for series, day in macro_calendar.ALL_MACRO_EVENTS:
        if not start <= day <= end:
            continue
        # The feed already lists this release with its forecast; show that row instead.
        if (series, day) in feed_series_days:
            continue
        at = MACRO_TIMES_ET[series]
        items.append(
            CalendarItem(
                id=f"macro:{series}:{day.isoformat()}",
                kind="macro",
                title=series[0].upper() + series[1:],
                date=day.isoformat(),
                time_et=at.strftime("%H:%M"),
                starts_at=_starts_at_utc(day, at),
                days_until=(day - today).days,
                impact=HIGH_IMPACT,
                source="table",
                source_label=TABLE_LABEL,
                # Only a day the feed actually covers can say "the feed has no such event".
                confirmed_by_feed=(
                    False
                    if econ.available and econ.covers_from and econ.covers_to and econ.covers_from <= day <= econ.covers_to
                    else None
                ),
            )
        )
    return items


def _open_positions(session: Session) -> list[PaperPosition]:
    return list(session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all())


def _fetch_earnings_dates(symbols: list[str], provider: DataProvider) -> tuple[dict[str, date], int]:
    """symbol -> next earnings date (None results dropped), and how many lookups failed.
    A symbol with no scheduled date is not a failure; a provider error is."""
    found: dict[str, date] = {}
    failed = 0

    def one(symbol: str) -> tuple[str, date | None, bool]:
        try:
            return symbol, provider.get_earnings_date(symbol), False
        except Exception as exc:  # noqa: BLE001 - one symbol's failure must not drop the rest
            logger.info("Calendar: no earnings date for %s: %s", symbol, exc)
            return symbol, None, True

    if not symbols:
        return found, failed
    with ThreadPoolExecutor(max_workers=EARNINGS_WORKERS) as pool:
        for symbol, day, errored in pool.map(one, symbols):
            if errored:
                failed += 1
            elif day is not None:
                found[symbol] = day
    return found, failed


def _earnings_symbols(requested: list[str], held: list[str]) -> list[str]:
    try:
        watchlist = [e.symbol for e in universe.load_universe()]
    except Exception as exc:  # noqa: BLE001 - a broken watchlist file must not blank the page
        logger.warning("Calendar: could not read the watchlist: %s", exc)
        watchlist = []
    ordered = list(dict.fromkeys(held + requested + watchlist[:MAX_EARNINGS_SYMBOLS]))
    # Crypto pairs have no earnings.
    return [s for s in ordered if not s.endswith("-USD")]


def build_calendar(
    session: Session,
    data_provider: DataProvider,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
    symbols: list[str] | None = None,
    now: datetime | None = None,
    econ: EconCalendarResult | None = None,
) -> CalendarResponse:
    """`now` (UTC) and `econ` are test seams; in production the clock is real and
    the feed result comes from the cached client."""
    today = _today_et(now)
    start = from_date or today
    end = to_date or (start + timedelta(days=DEFAULT_RANGE_DAYS - 1))
    if end < start:
        raise CalendarRangeError("'to' must not be before 'from'.")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise CalendarRangeError(f"The range may span at most {MAX_RANGE_DAYS} days.")

    positions = _open_positions(session)
    held = list(dict.fromkeys(p.symbol for p in positions))
    horizon_end = today + timedelta(days=POSITION_HORIZON_DAYS)

    # ---- economic feed + built-in table -------------------------------------
    econ = econ if econ is not None else get_econ_calendar()
    feed_events = [e for e in econ.events if start <= e.date_et <= end] if econ.available else []
    feed_series_days: set[tuple[str, date]] = set()
    for event in econ.events if econ.available else []:
        series = macro_series_for_title(event.title)
        if series:
            feed_series_days.add((series, event.date_et))
    table_dates = {(label, d) for label, d in macro_calendar.ALL_MACRO_EVENTS}

    items: list[CalendarItem] = []
    for event in feed_events:
        series = macro_series_for_title(event.title)
        items.append(_economic_item(event, today, (series, event.date_et) in table_dates if series else None))
    items += _macro_items(start, end, today, feed_series_days, econ)

    # ---- earnings ------------------------------------------------------------
    earn_symbols = _earnings_symbols(symbols or [], held)
    earn_dates, earn_failed = _fetch_earnings_dates(earn_symbols, data_provider)
    for symbol, day in earn_dates.items():
        if start <= day <= end:
            items.append(
                CalendarItem(
                    id=f"earnings:{symbol}:{day.isoformat()}",
                    kind="earnings",
                    title=f"{symbol} earnings",
                    date=day.isoformat(),
                    days_until=(day - today).days,
                    symbol=symbol,
                    source="provider",
                    source_label=PROVIDER_LABEL,
                    position_symbols=[symbol] if symbol in held else [],
                )
            )

    # ---- "my positions" flags -----------------------------------------------
    for item in items:
        if item.kind != "earnings" and held and 0 <= item.days_until <= POSITION_HORIZON_DAYS:
            if item.kind == "macro" or item.impact == HIGH_IMPACT:
                item.position_symbols = held
        item.my_position = bool(item.position_symbols)

    items.sort(key=lambda i: (i.date, i.time_et or "", i.kind, i.title))

    # ---- catalysts per open position -----------------------------------------
    position_catalysts: list[PositionCatalysts] = []
    macro_ahead = sorted(
        {
            (day, label)
            for label, day in macro_calendar.ALL_MACRO_EVENTS
            if today <= day <= horizon_end
        }
    )
    for position in positions:
        entries: list[CatalystEntry] = []
        earn = earn_dates.get(position.symbol)
        if earn is not None and today <= earn <= horizon_end:
            entries.append(
                CatalystEntry(kind="earnings", title=f"{position.symbol} earnings", date=earn.isoformat(), days_until=(earn - today).days)
            )
        for day, label in macro_ahead:
            entries.append(
                CatalystEntry(kind="macro", title=label[0].upper() + label[1:], date=day.isoformat(), days_until=(day - today).days)
            )
        entries.sort(key=lambda c: (c.date, c.kind))
        position_catalysts.append(PositionCatalysts(symbol=position.symbol, direction=position.direction, catalysts=entries))

    # ---- source status -------------------------------------------------------
    sources = [_economic_status(econ), _macro_status(start, end), _earnings_status(len(earn_symbols), earn_failed)]
    mismatches = [
        MacroMismatchSchema(
            series=m.series,
            table_date=m.table_date.isoformat() if m.table_date else None,
            feed_date=m.feed_date.isoformat() if m.feed_date else None,
            message=m.message,
        )
        for m in compare_with_macro_table(econ)
    ]

    return CalendarResponse(
        from_date=start.isoformat(),
        to_date=end.isoformat(),
        today=today.isoformat(),
        items=items,
        position_catalysts=position_catalysts,
        sources=sources,
        mismatches=mismatches,
        earnings_symbols_checked=len(earn_symbols),
        generated_at=utcnow_naive().isoformat() + "Z",
    )


def _economic_status(econ: EconCalendarResult) -> SourceStatus:
    if not econ.available:
        return SourceStatus(
            key="economic",
            label=FEED_LABEL,
            status="unavailable",
            detail=(
                "economic calendar source unavailable: showing the built-in Fed/CPI/jobs dates only"
                + (f" ({econ.detail})" if econ.detail else "")
            ),
        )
    span = f"covers {econ.covers_from.isoformat()} to {econ.covers_to.isoformat()}" if econ.covers_from and econ.covers_to else None
    if econ.stale:
        return SourceStatus(key="economic", label=FEED_LABEL, status="partial", detail=econ.detail)
    return SourceStatus(
        key="economic",
        label=FEED_LABEL,
        status="ok",
        detail=(span + "; it only lists the current week") if span else "it only lists the current week",
    )


def _macro_status(start: date, end: date) -> SourceStatus:
    missing = sorted({*macro_calendar.uncovered_macro_series(start), *macro_calendar.uncovered_macro_series(end)})
    if missing:
        return SourceStatus(
            key="macro",
            label=TABLE_LABEL,
            status="partial",
            detail="no dates listed yet for: " + ", ".join(missing) + " (the agencies have not published them or the table needs extending)",
        )
    return SourceStatus(key="macro", label=TABLE_LABEL, status="ok", detail="hand-kept from the Fed and BLS schedules")


def _earnings_status(checked: int, failed: int) -> SourceStatus:
    if checked == 0:
        return SourceStatus(key="earnings", label=PROVIDER_LABEL, status="ok", detail="no symbols to check")
    if failed >= checked:
        return SourceStatus(key="earnings", label=PROVIDER_LABEL, status="unavailable", detail=f"earnings dates could not be read for any of {checked} symbols")
    if failed / checked > EARNINGS_PARTIAL_FAILURE_SHARE:
        return SourceStatus(key="earnings", label=PROVIDER_LABEL, status="partial", detail=f"earnings dates could not be read for {failed} of {checked} symbols")
    return SourceStatus(key="earnings", label=PROVIDER_LABEL, status="ok", detail=f"{checked} symbols checked")
