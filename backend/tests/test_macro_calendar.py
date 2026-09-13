"""Macro event proximity — a symbol-independent, one-directional risk flag.

The dates themselves are hand-maintained fact (verified 2026-09-13 against
federalreserve.gov and bls.gov — see macro_calendar.py's module docstring for
the exact source URLs), so these tests exercise the proximity logic, not the
calendar's content.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.analysis.macro_calendar import (
    ALL_MACRO_EVENTS,
    MACRO_EVENT_SCORE_CAP,
    score_macro_event_proximity,
)


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
