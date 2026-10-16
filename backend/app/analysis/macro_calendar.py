from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# Broad, market-wide event risk — same conceptual family as score_vix_regime
# (a one-directional risk flag, not symbol-specific confluence) but a
# DIFFERENT dimension: VIX reads current pricing of fear, this reads the
# calendar for scheduled events that can move the whole tape regardless of
# what any individual chart looks like. A CPI or FOMC surprise gaps every
# stock through its stop the same morning; that is a real, knowable-in-advance
# risk that has nothing to do with predicting what the report will say.
#
# Dates below are hand-maintained from each agency's own published schedule —
# same maintenance model this project already uses for `data/sp500.csv`, not
# a live calendar feed (there isn't a free one). Verified directly against:
#   FOMC:  https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
#          (2026 verified 2026-09-13; 2026 and 2027 both read from that page on
#          2026-10-01, which then showed "Last Update: September 16, 2026" and
#          listed every 2027 meeting. The policy statement + press conference
#          come on the SECOND day of each two-day meeting, so that is the date
#          stored. The Fed marks each date tentative until the meeting before it.)
#   CPI:   https://www.bls.gov/schedule/news_release/cpi.htm
#          (2026 verified 2026-09-13 and again 2026-10-01; BLS had NOT yet
#          published any 2027 release date on 2026-10-01 — the table ended with
#          the November-2026 CPI released 2026-12-10, and
#          https://www.bls.gov/schedule/2027/home.htm returned 404.)
#   NFP:   https://www.bls.gov/schedule/news_release/empsit.htm ("Employment
#          Situation"; same state on 2026-10-01: last row is the
#          November-2026 report released 2026-12-04, nothing for 2027.)
# bls.gov refuses scripted requests (403 "bots prohibited"), so its pages have
# to be read in a normal browser or a one-off page fetch, never from code here.
#
# So the table is PARTIAL for 2027: FOMC is complete through the end of 2027,
# CPI and jobs-report dates stop at the end of 2026. MACRO_SERIES_COVERAGE
# records, per series, the last day each one is actually known for, so the
# gap is visible to code (macro_calendar_covers / uncovered_macro_series and a
# once-per-process warning log) instead of silently scoring "no event nearby".
#
# TO DO: add the 2027 CPI and jobs-report dates as soon as BLS publishes them
# (expected in the last quarter of 2026), then bump their coverage end to
# 2027-12-31. Extend all three again for 2028 before 2027 ends (the Fed
# usually lists the following year already; BLS in autumn). A test fails when
# any series has under MIN_FUTURE_COVERAGE_DAYS of future coverage left, so
# the reminder cannot be missed.

FOMC_DATES_2026 = [
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
]

# Policy-statement day (second day of each meeting), all Wednesdays. Source:
# the Fed's FOMC calendars page, read 2026-10-01 (meetings 26-27 Jan, 16-17 Mar,
# 27-28 Apr, 8-9 Jun, 27-28 Jul, 14-15 Sep, 26-27 Oct, 7-8 Dec).
FOMC_DATES_2027 = [
    date(2027, 1, 27),
    date(2027, 3, 17),
    date(2027, 4, 28),
    date(2027, 6, 9),
    date(2027, 7, 28),
    date(2027, 9, 15),
    date(2027, 10, 27),
    date(2027, 12, 8),
]

CPI_RELEASE_DATES_2026 = [
    date(2026, 1, 13),
    date(2026, 2, 13),
    date(2026, 3, 11),
    date(2026, 4, 10),
    date(2026, 5, 12),
    date(2026, 6, 10),
    date(2026, 7, 14),
    date(2026, 8, 12),
    date(2026, 9, 11),
    date(2026, 10, 14),
    date(2026, 11, 10),
    date(2026, 12, 10),
]

NFP_RELEASE_DATES_2026 = [
    date(2026, 1, 9),
    date(2026, 2, 11),
    date(2026, 3, 6),
    date(2026, 4, 3),
    date(2026, 5, 8),
    date(2026, 6, 5),
    date(2026, 7, 2),
    date(2026, 8, 7),
    date(2026, 9, 4),
    date(2026, 10, 2),
    date(2026, 11, 6),
    date(2026, 12, 4),
]

ALL_MACRO_EVENTS: list[tuple[str, date]] = (
    [("FOMC decision", d) for d in FOMC_DATES_2026 + FOMC_DATES_2027]
    + [("CPI release", d) for d in CPI_RELEASE_DATES_2026]
    + [("jobs report", d) for d in NFP_RELEASE_DATES_2026]
)

# Per series: the first and last day the table above actually covers. A date
# outside a series' span means "not tracked", which is different from "no
# event that day" — the scorer returns 0 for both, so this record is what keeps
# the first from passing as the second. The end is the last day of the last
# year whose full schedule is listed, NOT the last listed event: BLS lists
# CPI/jobs dates a year at a time, so 2026-12-31 is covered even though the
# final 2026 CPI release is 12-10.
MACRO_SERIES_COVERAGE: dict[str, tuple[date, date]] = {
    "FOMC decision": (date(2026, 1, 1), date(2027, 12, 31)),
    "CPI release": (date(2026, 1, 1), date(2026, 12, 31)),
    "jobs report": (date(2026, 1, 1), date(2026, 12, 31)),
}

# The coverage test fails when any series has less than this left, so an
# extension is prompted weeks before the silence starts rather than after.
MIN_FUTURE_COVERAGE_DAYS = 60

# Same shape/cap as score_vix_regime: one-directional, small, never a bonus
# for "no event nearby" (that is just the normal state, not a signal).
MACRO_EVENT_SCORE_CAP = 1
# A stop set today can still be live tomorrow morning when the number drops —
# so "imminent" includes the day before, not just the day of.
MACRO_EVENT_LOOKAHEAD_DAYS = 1


def uncovered_macro_series(day: date) -> list[str]:
    """Names of the tracked series whose table does not cover `day`."""
    return [
        label for label, (start, end) in MACRO_SERIES_COVERAGE.items()
        if not start <= day <= end
    ]


def macro_calendar_covers(day: date) -> bool:
    """True when every tracked series (FOMC, CPI, jobs report) has its schedule
    listed for `day`. False means a 0 from score_macro_event_proximity on that
    day is "not tracked", not "confirmed calm"."""
    return not uncovered_macro_series(day)


def macro_coverage_days_left(today: date | None = None) -> dict[str, int]:
    """Days from `today` to the end of each series' coverage (negative once it
    has run out)."""
    today = today or date.today()
    return {label: (end - today).days for label, (_, end) in MACRO_SERIES_COVERAGE.items()}


# Series already warned about in this process, so the log says it once rather
# than once per trade plan.
_warned_uncovered: set[str] = set()


def _warn_once_if_uncovered(day: date) -> None:
    missing = [label for label in uncovered_macro_series(day) if label not in _warned_uncovered]
    if not missing:
        return
    _warned_uncovered.update(missing)
    logger.warning(
        "Macro-event calendar has no %s dates for %s; the macro-event penalty is "
        "silently off for those events. Extend analysis/macro_calendar.py from the "
        "agencies' published schedules.",
        ", ".join(missing), day.isoformat(),
    )


def score_macro_event_proximity(today: date | None = None) -> tuple[int, list[str]]:
    """-MACRO_EVENT_SCORE_CAP when a scheduled macro release falls within
    MACRO_EVENT_LOOKAHEAD_DAYS, else 0. Symbol-independent by design — this
    is the same penalty for every plan generated on a given day, which is
    correct: the risk is to the whole tape, not to any one setup.

    Falls outside the hand-maintained table (see MACRO_SERIES_COVERAGE)
    contributes 0 rather than guessing — silence here means "not tracked,"
    never "confirmed calm." The gap is logged once per process, and
    macro_calendar_covers() lets a caller tell the two apart.
    """
    today = today or date.today()
    _warn_once_if_uncovered(today + timedelta(days=MACRO_EVENT_LOOKAHEAD_DAYS))
    horizon_end = today + timedelta(days=MACRO_EVENT_LOOKAHEAD_DAYS)
    upcoming = [(label, d) for label, d in ALL_MACRO_EVENTS if today <= d <= horizon_end]
    if not upcoming:
        return 0, []

    label, event_date = min(upcoming, key=lambda pair: pair[1])
    when = "today" if event_date == today else "tomorrow"
    return -MACRO_EVENT_SCORE_CAP, [f"{label} {when} ({event_date.isoformat()}) — market-wide event risk"]
