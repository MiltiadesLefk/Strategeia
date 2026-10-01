# Copyright (c) 2026 OpenTerminal contributors (MIT License; full text in THIRD_PARTY_NOTICES.md)
# Adapted from ErTasselli/OpenTerminal@95618ee server/src/providers/econcalendar.ts;
# changes: ported TypeScript -> Python; kept only the weekly Forex Factory JSON feed
# (no FRED "actual" backfill); events are filtered to US releases, stored as UTC and
# also shown in Eastern time; the feed is cached for hours with the last good copy
# served when it fails (it rate-limits and sometimes answers with an empty body);
# and a cross-check against our hand-kept Fed/CPI/jobs-report table was added.
"""Economic calendar: this week's scheduled US releases with forecast and previous values.

The one source is Forex Factory's free public weekly export,

    https://nfs.faireconomy.media/ff_calendar_thisweek.json

It needs no key. It only ever holds the current Monday-to-Sunday week, it never
carries the released "actual" value, and it is an unofficial feed that can answer
with an empty body when asked too often. So this module:

- keeps the answer for hours (the schedule for a week does not change often);
- serves the last good copy, labelled with its age, when a refresh fails;
- remembers a failure for a few minutes so a broken feed is not hammered;
- never invents an event: no feed means no economic events, and the caller is told.

`compare_with_macro_table` uses the feed to double-check the dates in
`analysis/macro_calendar.py` (Fed decisions, CPI, jobs report). The hand table is
never changed by it: a disagreement is reported for a person to look at.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from threading import Lock
from typing import Any, Callable

import httpx

from app.analysis import macro_calendar
from app.markets import US_MARKET_TZ

logger = logging.getLogger(__name__)

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FEED_LABEL = "Forex Factory weekly feed"
FEED_TIMEOUT_SECONDS = 10.0
# A week's schedule barely moves once published, and the feed rate-limits, so
# a successful read is good for hours.
FEED_TTL_SECONDS = 3 * 60 * 60
# After a failed read, do not ask again for this long: an empty answer is usually
# the rate limit, and asking again straight away only prolongs it.
FEED_FAILURE_BACKOFF_SECONDS = 10 * 60
# A copy older than this is not shown as a stand-in for a failed refresh: by
# then it describes a different week.
FEED_STALE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

US_COUNTRY_CODE = "USD"
IMPACTS = ("Low", "Medium", "High", "Holiday")


@dataclass(frozen=True)
class EconEvent:
    title: str
    country: str  # "US"
    starts_at: datetime  # naive UTC
    date_et: date
    time_et: str | None  # "08:30", or None for an all-day entry
    impact: str  # Low | Medium | High | Holiday
    forecast: str | None
    previous: str | None
    actual: str | None = None  # the feed never has it; kept so the page can show a gap honestly


@dataclass
class EconCalendarResult:
    events: list[EconEvent] = field(default_factory=list)
    available: bool = False
    # Why it is unavailable (or served from an old copy); shown to the user.
    detail: str | None = None
    fetched_at: datetime | None = None  # naive UTC, when the events were read
    stale: bool = False  # the refresh failed and these are from an earlier read
    covers_from: date | None = None  # first and last day (Eastern) the feed listed
    covers_to: date | None = None


@dataclass(frozen=True)
class MacroMismatch:
    series: str  # "FOMC decision" | "CPI release" | "jobs report"
    table_date: date | None
    feed_date: date | None
    message: str


# ---------------------------------------------------------------- fetching ----

_state_lock = Lock()
_cached: EconCalendarResult | None = None
_cached_at_monotonic: float | None = None
_failed_at_monotonic: float | None = None
_last_error: str | None = None


def reset_econ_calendar_cache() -> None:
    """Forget the cached feed and any failure backoff (tests; a manual refresh)."""
    global _cached, _cached_at_monotonic, _failed_at_monotonic, _last_error
    with _state_lock:
        _cached = None
        _cached_at_monotonic = None
        _failed_at_monotonic = None
        _last_error = None


def _http_get_json(url: str) -> Any:
    """The one network call. Raises on anything that is not a JSON list."""
    response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=FEED_TIMEOUT_SECONDS)
    response.raise_for_status()
    if not response.content.strip():
        raise ValueError("the feed answered with an empty body (usually its rate limit)")
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("the feed did not return a list of events")
    return data


def parse_feed(raw: list[dict]) -> list[EconEvent]:
    """US events only, sorted by time. A row that cannot be read is skipped, not guessed."""
    events: list[EconEvent] = []
    for row in raw:
        try:
            if row.get("country") != US_COUNTRY_CODE:
                continue
            when = datetime.fromisoformat(str(row["date"]))
            if when.tzinfo is None:
                # The feed always carries an offset; without one we cannot place it in time.
                continue
            et = when.astimezone(US_MARKET_TZ)
            impact = str(row.get("impact") or "Low")
            if impact not in IMPACTS:
                impact = "Low"
            # Holidays and "all day" rows come through at midnight Eastern.
            all_day = impact == "Holiday" or (et.hour == 0 and et.minute == 0)
            events.append(
                EconEvent(
                    title=str(row["title"]).strip(),
                    country="US",
                    starts_at=when.astimezone(timezone.utc).replace(tzinfo=None),
                    date_et=et.date(),
                    time_et=None if all_day else et.strftime("%H:%M"),
                    impact=impact,
                    forecast=(str(row.get("forecast") or "").strip() or None),
                    previous=(str(row.get("previous") or "").strip() or None),
                )
            )
        except (KeyError, ValueError, TypeError, AttributeError):
            logger.info("Skipping an unreadable economic-calendar row: %r", row)
    events.sort(key=lambda e: (e.starts_at, e.title))
    return events


def get_econ_calendar(
    *,
    fetch: Callable[[str], Any] | None = None,
    now: datetime | None = None,
) -> EconCalendarResult:
    """This week's US events, from cache when fresh. Never raises: a failure comes
    back as `available=False` (or `stale=True` when an earlier copy is still usable).

    `fetch` and `now` are test seams (the clock is naive UTC)."""
    global _cached, _cached_at_monotonic, _failed_at_monotonic, _last_error
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    mono = time.monotonic()

    with _state_lock:
        if _cached is not None and _cached_at_monotonic is not None and mono - _cached_at_monotonic < FEED_TTL_SECONDS:
            return _cached
        if _failed_at_monotonic is not None and mono - _failed_at_monotonic < FEED_FAILURE_BACKOFF_SECONDS:
            return _unavailable_or_stale(_last_error or "the feed failed recently", now)

    try:
        raw = (fetch or _http_get_json)(FEED_URL)
        events = parse_feed(raw)
    except Exception as exc:  # noqa: BLE001 - any failure of an optional feed degrades the same way
        reason = _short_reason(exc)
        logger.warning("Economic calendar feed unavailable: %s", reason)
        with _state_lock:
            _failed_at_monotonic = mono
            _last_error = reason
            return _unavailable_or_stale(reason, now)

    result = EconCalendarResult(
        events=events,
        available=True,
        fetched_at=now,
        covers_from=min((e.date_et for e in events), default=None),
        covers_to=max((e.date_et for e in events), default=None),
    )
    with _state_lock:
        _cached = result
        _cached_at_monotonic = mono
        _failed_at_monotonic = None
        _last_error = None
    return result


def _short_reason(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text[:200]


def _unavailable_or_stale(reason: str, now: datetime) -> EconCalendarResult:
    """Caller holds the lock. An earlier good copy, if recent enough, beats nothing."""
    previous = _cached
    if previous is not None and previous.fetched_at is not None:
        age = (now - previous.fetched_at).total_seconds()
        if age < FEED_STALE_MAX_AGE_SECONDS:
            return EconCalendarResult(
                events=previous.events,
                available=True,
                stale=True,
                detail=f"refresh failed ({reason}); showing the copy read {previous.fetched_at.isoformat()}Z",
                fetched_at=previous.fetched_at,
                covers_from=previous.covers_from,
                covers_to=previous.covers_to,
            )
    return EconCalendarResult(available=False, detail=reason)


# ------------------------------------------------------ cross-check vs table ----

# Which feed titles stand for each of the three series the hand table tracks.
# Matched on the feed's own wording; anything that does not match is simply not
# cross-checked (an unknown title is not a mismatch).
_SERIES_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("FOMC decision", re.compile(r"^(fomc statement|federal funds rate)$", re.I)),
    ("CPI release", re.compile(r"^(core )?cpi (m/m|y/y)$", re.I)),
    ("jobs report", re.compile(r"^non-?farm employment change$", re.I)),
]


def macro_series_for_title(title: str) -> str | None:
    """The hand-table series a feed event title belongs to, if any."""
    cleaned = title.strip()
    for series, pattern in _SERIES_PATTERNS:
        if pattern.match(cleaned):
            return series
    return None


def _table_dates(series: str) -> list[date]:
    return [d for label, d in macro_calendar.ALL_MACRO_EVENTS if label == series]


def compare_with_macro_table(result: EconCalendarResult) -> list[MacroMismatch]:
    """Disagreements between the feed and `macro_calendar.ALL_MACRO_EVENTS`, inside
    the days the feed actually covers. Reports only; the table is never edited.

    Two directions:
    - the feed lists a Fed/CPI/jobs event on a day the table has none for that
      series, though the table claims to cover that day;
    - the table lists an event inside the feed's window that the feed does not.
    """
    if not result.available or result.covers_from is None or result.covers_to is None:
        return []

    first, last = result.covers_from, result.covers_to
    feed_dates: dict[str, set[date]] = {}
    for event in result.events:
        series = macro_series_for_title(event.title)
        if series:
            feed_dates.setdefault(series, set()).add(event.date_et)

    mismatches: list[MacroMismatch] = []
    for series, (cover_start, cover_end) in macro_calendar.MACRO_SERIES_COVERAGE.items():
        table = set(_table_dates(series))
        seen = feed_dates.get(series, set())
        for day in sorted(seen):
            if cover_start <= day <= cover_end and day not in table:
                nearby = sorted(d for d in table if abs((d - day).days) <= 7)
                mismatches.append(
                    MacroMismatch(
                        series=series,
                        table_date=nearby[0] if nearby else None,
                        feed_date=day,
                        message=(
                            f"{series}: the feed lists {day.isoformat()}, the built-in table does not"
                            + (f" (it has {nearby[0].isoformat()})" if nearby else "")
                        ),
                    )
                )
        for day in sorted(table):
            if first <= day <= last and day not in seen:
                other = sorted(seen)
                mismatches.append(
                    MacroMismatch(
                        series=series,
                        table_date=day,
                        feed_date=other[0] if other else None,
                        message=(
                            f"{series}: the built-in table lists {day.isoformat()}, the feed does not"
                            + (f" (it has {other[0].isoformat()})" if other else " (it has none this week)")
                        ),
                    )
                )
    return mismatches
