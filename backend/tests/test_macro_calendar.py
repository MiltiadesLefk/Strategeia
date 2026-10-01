"""Macro event proximity — a symbol-independent, one-directional risk flag.

The dates themselves are hand-maintained fact (2026 verified 2026-09-13 and
FOMC 2027 verified 2026-10-01 against federalreserve.gov and bls.gov — see
macro_calendar.py's header comment for the exact source URLs), so most tests
exercise the proximity logic and the table's shape, plus a few spot checks of
the 2027 FOMC dates against the Fed's published meeting calendar.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from app.analysis import macro_calendar
from app.analysis.macro_calendar import (
    ALL_MACRO_EVENTS,
    CPI_RELEASE_DATES_2026,
    FOMC_DATES_2026,
    FOMC_DATES_2027,
    MACRO_EVENT_SCORE_CAP,
    MACRO_SERIES_COVERAGE,
    MIN_FUTURE_COVERAGE_DAYS,
    NFP_RELEASE_DATES_2026,
    macro_calendar_covers,
    macro_coverage_days_left,
    score_macro_event_proximity,
    uncovered_macro_series,
)
from app.markets import us_holiday_name


def test_no_event_nearby_contributes_nothing():
    # A date deliberately far from any tracked event.
    quiet_day = date(2026, 1, 20)
    assert score_macro_event_proximity(quiet_day) == (0, [])


def test_the_day_of_a_known_event_is_flagged():
    label, event_date = ALL_MACRO_EVENTS[0]
    score, reasons = score_macro_event_proximity(event_date)
    assert score == -MACRO_EVENT_SCORE_CAP
    assert label in reasons[0]
    assert "today" in reasons[0]


def test_the_day_before_a_known_event_is_also_flagged():
    """A stop set today is still live tomorrow morning when the number drops."""
    _, event_date = ALL_MACRO_EVENTS[0]
    score, reasons = score_macro_event_proximity(event_date - timedelta(days=1))
    assert score == -MACRO_EVENT_SCORE_CAP
    assert "tomorrow" in reasons[0]


def test_two_days_before_is_not_flagged():
    _, event_date = ALL_MACRO_EVENTS[0]
    assert score_macro_event_proximity(event_date - timedelta(days=2)) == (0, [])


def test_the_day_after_a_known_event_is_not_flagged():
    """The risk is to a position held INTO the release, not one already past it."""
    _, event_date = ALL_MACRO_EVENTS[0]
    assert score_macro_event_proximity(event_date + timedelta(days=1)) == (0, [])


def test_out_of_range_dates_contribute_nothing_not_a_guess():
    """A date the hand-maintained table doesn't cover (well outside 2026)
    must read as 'not tracked,' never as 'confirmed calm.'"""
    far_future = date(2031, 6, 15)
    assert score_macro_event_proximity(far_future) == (0, [])


def test_every_tracked_event_is_actually_flaggable():
    """Regression against a table edit that accidentally breaks the type or
    ordering of ALL_MACRO_EVENTS."""
    for label, event_date in ALL_MACRO_EVENTS:
        score, reasons = score_macro_event_proximity(event_date)
        assert score == -MACRO_EVENT_SCORE_CAP
        assert reasons
        assert isinstance(label, str) and label


# --- 2027 and table shape ---------------------------------------------------

def test_fomc_2027_matches_the_fed_published_calendar():
    """Second day of each meeting on the Fed's FOMC calendars page (read
    2026-10-01): 26-27 Jan, 16-17 Mar, 27-28 Apr, 8-9 Jun, 27-28 Jul, 14-15 Sep,
    26-27 Oct, 7-8 Dec."""
    assert FOMC_DATES_2027 == [
        date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28), date(2027, 6, 9),
        date(2027, 7, 28), date(2027, 9, 15), date(2027, 10, 27), date(2027, 12, 8),
    ]


def test_fomc_2027_dates_are_flagged_and_in_the_master_list():
    for d in FOMC_DATES_2027:
        assert ("FOMC decision", d) in ALL_MACRO_EVENTS
        score, reasons = score_macro_event_proximity(d)
        assert score == -MACRO_EVENT_SCORE_CAP
        assert "FOMC decision" in reasons[0]


def test_every_dated_list_is_sorted_unique_and_in_its_year():
    for dates, year in [
        (FOMC_DATES_2026, 2026), (FOMC_DATES_2027, 2027),
        (CPI_RELEASE_DATES_2026, 2026), (NFP_RELEASE_DATES_2026, 2026),
    ]:
        assert dates == sorted(dates)
        assert len(set(dates)) == len(dates)
        assert {d.year for d in dates} == {year}


def test_fomc_statements_fall_on_wednesdays():
    for d in FOMC_DATES_2026 + FOMC_DATES_2027:
        assert d.weekday() == 2, f"{d} is a {d.strftime('%A')}"


def test_no_event_is_on_a_weekend_or_holiday():
    """Good Friday is the one exception: it is a market holiday but not a
    federal one, and BLS does publish the jobs report that day (2026-04-03)."""
    for label, d in ALL_MACRO_EVENTS:
        assert d.weekday() < 5, f"{label} {d} is a weekend"
        assert us_holiday_name(d) in (None, "Good Friday"), f"{label} {d} is a holiday"


def test_series_counts_are_what_the_agencies_publish():
    """12 monthly releases a year for CPI and the jobs report, 8 FOMC meetings."""
    assert len(FOMC_DATES_2026) == len(FOMC_DATES_2027) == 8
    assert len(CPI_RELEASE_DATES_2026) == len(NFP_RELEASE_DATES_2026) == 12
    assert len(ALL_MACRO_EVENTS) == 16 + 24
    assert len(set(ALL_MACRO_EVENTS)) == len(ALL_MACRO_EVENTS)


def test_every_event_lies_inside_its_series_coverage():
    for label, d in ALL_MACRO_EVENTS:
        start, end = MACRO_SERIES_COVERAGE[label]
        assert start <= d <= end


# --- honest silence ---------------------------------------------------------

def test_calendar_covers_reports_partial_2027_coverage():
    assert macro_calendar_covers(date(2026, 10, 1))
    assert uncovered_macro_series(date(2026, 10, 1)) == []
    # FOMC runs through 2027, CPI and the jobs report stop at the end of 2026.
    assert not macro_calendar_covers(date(2027, 2, 10))
    assert uncovered_macro_series(date(2027, 2, 10)) == ["CPI release", "jobs report"]
    assert uncovered_macro_series(date(2028, 1, 5)) == list(MACRO_SERIES_COVERAGE)
    assert not macro_calendar_covers(date(2025, 12, 31))


def test_uncovered_day_still_scores_zero_but_logs_once(caplog):
    macro_calendar._warned_uncovered.clear()
    with caplog.at_level(logging.WARNING, logger=macro_calendar.logger.name):
        assert score_macro_event_proximity(date(2027, 2, 10)) == (0, [])
        assert score_macro_event_proximity(date(2027, 2, 11)) == (0, [])
    warnings_logged = [r for r in caplog.records if "no CPI release, jobs report dates" in r.getMessage()]
    assert len(warnings_logged) == 1


def test_covered_day_logs_nothing(caplog):
    macro_calendar._warned_uncovered.clear()
    with caplog.at_level(logging.WARNING, logger=macro_calendar.logger.name):
        score_macro_event_proximity(date(2026, 6, 1))
    assert not caplog.records


def test_calendar_has_enough_future_coverage():
    """Fails when the table is about to run out — the cue to re-read
    federalreserve.gov and bls.gov and extend macro_calendar.py (see its header).

    Deliberately tied to the real clock: a hand-kept calendar that silently
    expires is the failure this guards against. It starts failing for the CPI
    and jobs-report series about 60 days before 2026 ends if BLS's 2027 dates
    have not been added by then, and for FOMC about 60 days before 2027 ends."""
    short = {
        label: days for label, days in macro_coverage_days_left().items()
        if days < MIN_FUTURE_COVERAGE_DAYS
    }
    assert not short, (
        f"macro calendar has under {MIN_FUTURE_COVERAGE_DAYS} days of future coverage left "
        f"(days remaining per series: {short}); extend analysis/macro_calendar.py from the "
        "federalreserve.gov FOMC calendar and the bls.gov CPI / Employment Situation schedules"
    )


def test_coverage_days_left_counts_from_the_given_day():
    left = macro_coverage_days_left(date(2026, 12, 1))
    assert left["CPI release"] == 30
    assert left["FOMC decision"] == 395
    assert macro_coverage_days_left(date(2027, 1, 1))["jobs report"] == -1
