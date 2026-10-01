"""US market session calendar: regular hours, NYSE holidays and 1:00 pm early closes.

The bundled universe is US equities plus a handful of `-USD` crypto pairs, so
"can anything trade right now" is a US-session question for everything except
crypto, which never closes.

Holidays are computed from NYSE's own rules rather than kept as a hand-typed
table, so the calendar works for any year without anyone remembering to extend
it — unlike analysis/macro_calendar.py, whose dates have no rule behind them.
The rules were checked against NYSE's published calendar
(https://www.nyse.com/markets/hours-calendars, read 2026-09-28) for every date
in 2026, 2027 and 2028; tests/test_markets.py pins all three years.

Knowing holidays used to be dismissed as a cost question (a few wasted provider
calls on Thanksgiving). It stopped being one once opening a paper position was
gated on the session (plan.md F-6): a holiday the calendar doesn't know is a day
the engine would fill trades at the previous session's close.

Every function taking `now` accepts a tz-aware datetime or a naive one, which is
read as UTC — the convention for every timestamp this app stores (see
timeutil.utcnow_naive). `None` means the real current time.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

US_MARKET_TZ = ZoneInfo("America/New_York")
US_MARKET_OPEN = time(9, 30)
US_MARKET_CLOSE = time(16, 0)
# NYSE's scheduled short sessions (the day after Thanksgiving, and the day
# before Independence Day / Christmas when that day trades) end at 1:00 pm ET.
US_EARLY_CLOSE = time(13, 0)

# Daily bars for a session aren't final the instant the bell rings; give the
# provider a little room to settle the last bar before the post-close sweep
# stops running. Measured from the real close, so an early-close day's grace
# ends at 1:30 pm, not 4:30.
POST_CLOSE_GRACE = timedelta(minutes=30)

# yfinance spells 24/7 instruments as `<BASE>-USD` (BTC-USD, ETH-USD, ...).
CRYPTO_SUFFIX = "-USD"

# First year each of these was an NYSE holiday. MLK Day has been observed
# since 1998; Juneteenth was added by the 2021 NYSE Rule 7.2 amendment and
# first observed on Monday 20 June 2022. Earlier years simply trade on those
# dates, which matters once the backtester walks historical calendars.
MLK_DAY_FIRST_YEAR = 1998
JUNETEENTH_FIRST_YEAR = 2022

# One-off closures no rule can derive: national days of mourning, storms.
# Each is announced by the exchange a few days ahead; add the next one here by
# hand, from ICE's own press release (ir.theice.com), when it happens. Listed
# from 2018 on — earlier ones (Hurricane Sandy 2012, President Ford 2007, ...)
# predate any history the app currently evaluates.
SPECIAL_CLOSURES: dict[date, str] = {
    date(2018, 12, 5): "National Day of Mourning for President George H. W. Bush",
    date(2025, 1, 9): "National Day of Mourning for President Jimmy Carter",
}

# How far ahead next_us_open/next_us_close search. The longest modern run of
# non-trading days is 9/11 (four weekdays plus a weekend); two weeks covers
# any combination the rules and the table above can produce.
MAX_DAYS_TO_NEXT_SESSION = 14

MarketState = Literal["open", "pre", "after", "closed", "holiday"]

_MONDAY, _THURSDAY, _FRIDAY, _SATURDAY, _SUNDAY = 0, 3, 4, 5, 6


def is_always_on(symbol: str) -> bool:
    """True for instruments with no session at all (crypto pairs). Used to
    keep marking positions that can still move while equities are shut."""
    return symbol.upper().endswith(CRYPTO_SUFFIX)


def to_market_time(now: datetime | None = None) -> datetime:
    """`now` in New York time. A naive value is read as UTC, not as the
    server's local time: `datetime.astimezone()` on a naive datetime assumes
    local time, which on the user's Greek machine would shift every session
    boundary by two or three hours while looking perfectly plausible."""
    if now is None:
        return datetime.now(US_MARKET_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(US_MARKET_TZ)


def format_market_time(moment: datetime) -> str:
    """'Mon 28 Sep 09:45 ET' — the form every user-facing note uses. The
    exchange's own clock rather than the viewer's: the rule being explained
    ("redone at the next open") is defined in New York time."""
    et = to_market_time(moment)
    return f"{et:%a} {et.day} {et:%b %H:%M} ET"


# ------------------------------------------------------------------ holidays


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def easter_sunday(year: int) -> date:
    """Western (Gregorian) Easter Sunday, by the anonymous Gregorian computus
    (Meeus/Jones/Butcher). Good Friday is the only NYSE holiday tied to the
    lunar calendar, so this is the one date no weekday rule can produce."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month, day = divmod(h + el - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(holiday: date) -> date | None:
    """NYSE Rule 7.2: a holiday on a Saturday closes the market the Friday
    before, one on a Sunday closes it the Monday after — "unless unusual
    business conditions exist, such as the ending of a monthly or the yearly
    accounting period". Of this holiday set, only New Year's Day can put a
    Saturday holiday's Friday on a month end (31 December), so a Saturday
    New Year's Day is simply not observed: the market is open on 31 December
    2027 and closed on no day for 1 January 2028, exactly as NYSE's calendar
    lists it. Returns None for that case."""
    weekday = holiday.weekday()
    if weekday == _SATURDAY:
        friday = holiday - timedelta(days=1)
        return None if friday.month != holiday.month else friday
    if weekday == _SUNDAY:
        return holiday + timedelta(days=1)
    return holiday


@lru_cache(maxsize=None)
def us_market_holidays(year: int) -> dict[date, str]:
    """Every full-day NYSE closure in `year`, keyed by the day the market is
    actually shut (the observed date), with the holiday's name."""
    rules: list[tuple[date | None, str]] = [
        (_observed(date(year, 1, 1)), "New Year's Day"),
        (_nth_weekday(year, 1, _MONDAY, 3) if year >= MLK_DAY_FIRST_YEAR else None, "Martin Luther King Jr. Day"),
        (_nth_weekday(year, 2, _MONDAY, 3), "Washington's Birthday"),
        (easter_sunday(year) - timedelta(days=2), "Good Friday"),
        (_last_weekday(year, 5, _MONDAY), "Memorial Day"),
        (_observed(date(year, 6, 19)) if year >= JUNETEENTH_FIRST_YEAR else None, "Juneteenth"),
        (_observed(date(year, 7, 4)), "Independence Day"),
        (_nth_weekday(year, 9, _MONDAY, 1), "Labor Day"),
        (_nth_weekday(year, 11, _THURSDAY, 4), "Thanksgiving Day"),
        (_observed(date(year, 12, 25)), "Christmas Day"),
    ]
    holidays = {day: name for day, name in rules if day is not None and day.year == year}
    holidays.update({day: name for day, name in SPECIAL_CLOSURES.items() if day.year == year})
    return holidays


@lru_cache(maxsize=None)
def us_early_closes(year: int) -> dict[date, str]:
    """NYSE's scheduled 1:00 pm closes in `year`: the day after Thanksgiving
    always, and 3 July / 24 December only when that day is a Monday to
    Thursday. On a Friday each is itself the observed holiday (4 July or
    Christmas on a Saturday); on a weekend there is no session to shorten.
    That is why 2026 has no July early close and 2027 has neither."""
    closes = {_nth_weekday(year, 11, _THURSDAY, 4) + timedelta(days=1): "the day after Thanksgiving"}
    for day, name in ((date(year, 7, 3), "Independence Day eve"), (date(year, 12, 24), "Christmas Eve")):
        if day.weekday() <= _THURSDAY:
            closes[day] = name
    holidays = us_market_holidays(year)
    return {day: name for day, name in closes.items() if day not in holidays}


def us_holiday_name(day: date) -> str | None:
    return us_market_holidays(day.year).get(day)


def is_us_trading_day(day: date) -> bool:
    return day.weekday() < _SATURDAY and day not in us_market_holidays(day.year)


# ------------------------------------------------------------------ sessions


@dataclass(frozen=True)
class SessionBounds:
    open: datetime  # tz-aware, New York
    close: datetime  # 1:00 pm on an early-close day
    early_close: bool


def us_session_bounds(day: date) -> SessionBounds | None:
    """The regular session on `day`, or None when the market doesn't open."""
    if not is_us_trading_day(day):
        return None
    early = day in us_early_closes(day.year)
    return SessionBounds(
        open=datetime.combine(day, US_MARKET_OPEN, tzinfo=US_MARKET_TZ),
        close=datetime.combine(day, US_EARLY_CLOSE if early else US_MARKET_CLOSE, tzinfo=US_MARKET_TZ),
        early_close=early,
    )


def is_us_market_open(now: datetime | None = None) -> bool:
    """Regular US cash session: DST-aware, holiday-aware, early-close-aware.
    Half-open — 09:30:00 is open, the closing bell itself is not, since
    nothing trades in the regular session once it has rung."""
    now_et = to_market_time(now)
    bounds = us_session_bounds(now_et.date())
    return bounds is not None and bounds.open <= now_et < bounds.close


def is_within_post_close_grace(now: datetime | None = None) -> bool:
    """True in the window just after the close (the early close, on a short
    day), so the final daily bar gets marked once it settles rather than
    waiting for the next session."""
    now_et = to_market_time(now)
    bounds = us_session_bounds(now_et.date())
    return bounds is not None and bounds.close <= now_et <= bounds.close + POST_CLOSE_GRACE


def is_market_open_for(symbol: str, now: datetime | None = None) -> bool:
    """Can `symbol` trade at `now`? Crypto always; everything else only in
    the US session. The one question every path that opens a position asks
    (PaperTradingEngine.open_position)."""
    return is_always_on(symbol) or is_us_market_open(now)


def _sessions_from(day: date) -> Iterator[SessionBounds]:
    for offset in range(MAX_DAYS_TO_NEXT_SESSION + 1):
        bounds = us_session_bounds(day + timedelta(days=offset))
        if bounds is not None:
            yield bounds


def _next_session_where(now_et: datetime, edge: Literal["open", "close"]) -> SessionBounds:
    """The first session whose `edge` (its open or its close) is still ahead of `now_et`."""
    for bounds in _sessions_from(now_et.date()):
        if (bounds.open if edge == "open" else bounds.close) > now_et:
            return bounds
    raise RuntimeError(f"No US session within {MAX_DAYS_TO_NEXT_SESSION} days of {now_et:%Y-%m-%d}")


def next_us_open(now: datetime | None = None) -> datetime:
    """The first session open strictly after `now` (New York time). During a
    session that's the NEXT trading day's open, not the one already under way."""
    return _next_session_where(to_market_time(now), "open").open


def next_us_close(now: datetime | None = None) -> datetime:
    """The first session close strictly after `now`: today's close while the
    market is open or before it opens, else the next trading day's."""
    return _next_session_where(to_market_time(now), "close").close


def us_closure_reason(now: datetime | None = None) -> str:
    """Why the US market is shut at `now`, in a few plain words for a note
    ("weekend", "Thanksgiving Day", "after the close"). Only meaningful while
    it IS shut."""
    now_et = to_market_time(now)
    holiday = us_holiday_name(now_et.date())
    if holiday is not None and now_et.weekday() < _SATURDAY:
        return holiday
    bounds = us_session_bounds(now_et.date())
    if bounds is None:
        return "weekend"
    if now_et < bounds.open:
        return "before the open"
    return "after the 1:00 pm early close" if bounds.early_close else "after the close"


@dataclass(frozen=True)
class MarketSession:
    """Everything the UI's market-status badge and Execute button need, from
    the one calendar (GET /api/market/session)."""

    state: MarketState
    is_open: bool
    next_open: datetime
    next_close: datetime
    next_close_is_early: bool
    # Set only when the market is shut today for a holiday (state "holiday").
    holiday_name: str | None


def us_market_session(now: datetime | None = None) -> MarketSession:
    now_et = to_market_time(now)
    today = now_et.date()
    bounds = us_session_bounds(today)
    holiday = us_holiday_name(today) if today.weekday() < _SATURDAY else None
    state: MarketState
    if bounds is None:
        state = "holiday" if holiday is not None else "closed"
    elif now_et < bounds.open:
        state = "pre"
    elif now_et < bounds.close:
        state = "open"
    else:
        state = "after"
    closing = _next_session_where(now_et, "close")
    return MarketSession(
        state=state,
        is_open=state == "open",
        next_open=_next_session_where(now_et, "open").open,
        next_close=closing.close,
        next_close_is_early=closing.early_close,
        holiday_name=holiday if state == "holiday" else None,
    )
