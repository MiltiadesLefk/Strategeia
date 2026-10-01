"""The US session calendar (app/markets.py).

The holiday and early-close tables below are copied from NYSE's own published
calendar (https://www.nyse.com/markets/hours-calendars, read 2026-09-28), which
lists 2026, 2027 and 2028. The calendar is computed from rules, so these tests
are what proves the rules reproduce the exchange's dates exactly — including
the awkward ones: Independence Day and Christmas observed on a Friday, and New
Year's Day 2028 falling on a Saturday and not being observed at all.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.markets import (
    JUNETEENTH_FIRST_YEAR,
    MLK_DAY_FIRST_YEAR,
    easter_sunday,
    format_market_time,
    is_market_open_for,
    is_us_market_open,
    is_us_trading_day,
    is_within_post_close_grace,
    next_us_close,
    next_us_open,
    to_market_time,
    us_closure_reason,
    us_early_closes,
    us_market_holidays,
    us_market_session,
    us_session_bounds,
)

NYSE_HOLIDAYS = {
    2026: {
        date(2026, 1, 1): "New Year's Day",
        date(2026, 1, 19): "Martin Luther King Jr. Day",
        date(2026, 2, 16): "Washington's Birthday",
        date(2026, 4, 3): "Good Friday",
        date(2026, 5, 25): "Memorial Day",
        date(2026, 6, 19): "Juneteenth",
        date(2026, 7, 3): "Independence Day",  # observed: 4 July is a Saturday
        date(2026, 9, 7): "Labor Day",
        date(2026, 11, 26): "Thanksgiving Day",
        date(2026, 12, 25): "Christmas Day",
    },
    2027: {
        date(2027, 1, 1): "New Year's Day",
        date(2027, 1, 18): "Martin Luther King Jr. Day",
        date(2027, 2, 15): "Washington's Birthday",
        date(2027, 3, 26): "Good Friday",
        date(2027, 5, 31): "Memorial Day",
        date(2027, 6, 18): "Juneteenth",  # observed: 19 June is a Saturday
        date(2027, 7, 5): "Independence Day",  # observed: 4 July is a Sunday
        date(2027, 9, 6): "Labor Day",
        date(2027, 11, 25): "Thanksgiving Day",
        date(2027, 12, 24): "Christmas Day",  # observed: 25 December is a Saturday
    },
    2028: {
        # No New Year's Day: 1 January 2028 is a Saturday, and NYSE doesn't
        # close on Friday 31 December 2027 (the year-end exception).
        date(2028, 1, 17): "Martin Luther King Jr. Day",
        date(2028, 2, 21): "Washington's Birthday",
        date(2028, 4, 14): "Good Friday",
        date(2028, 5, 29): "Memorial Day",
        date(2028, 6, 19): "Juneteenth",
        date(2028, 7, 4): "Independence Day",
        date(2028, 9, 4): "Labor Day",
        date(2028, 11, 23): "Thanksgiving Day",
        date(2028, 12, 25): "Christmas Day",
    },
}

# Each closes at 1:00 pm ET. 2026 has no July early close (4 July is itself
# observed on Friday 3 July); 2027 has neither July nor Christmas Eve.
NYSE_EARLY_CLOSES = {
    2026: {date(2026, 11, 27), date(2026, 12, 24)},
    2027: {date(2027, 11, 26)},
    2028: {date(2028, 7, 3), date(2028, 11, 24)},
}


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# ------------------------------------------------------- the published table


@pytest.mark.parametrize("year", sorted(NYSE_HOLIDAYS))
def test_holidays_match_nyse_published_calendar(year):
    assert us_market_holidays(year) == NYSE_HOLIDAYS[year]


@pytest.mark.parametrize("year", sorted(NYSE_EARLY_CLOSES))
def test_early_closes_match_nyse_published_calendar(year):
    assert set(us_early_closes(year)) == NYSE_EARLY_CLOSES[year]


@pytest.mark.parametrize("year", sorted(NYSE_EARLY_CLOSES))
def test_early_close_days_end_at_one_pm_new_york_time(year):
    for day in NYSE_EARLY_CLOSES[year]:
        bounds = us_session_bounds(day)
        assert bounds is not None and bounds.early_close
        assert (bounds.close.hour, bounds.close.minute) == (13, 0)
        assert bounds.close.tzinfo is not None and bounds.close.utcoffset() is not None


def test_every_holiday_is_a_non_trading_day_and_the_day_before_new_year_2028_trades():
    for holidays in NYSE_HOLIDAYS.values():
        for day in holidays:
            assert not is_us_trading_day(day)
            assert us_session_bounds(day) is None
    assert is_us_trading_day(date(2027, 12, 31))  # the year-end exception


# ------------------------------------------------------ observed-date rules


def test_sunday_holidays_move_to_monday():
    assert date(2022, 12, 26) in us_market_holidays(2022)  # Christmas 2022 was a Sunday
    assert date(2023, 1, 2) in us_market_holidays(2023)  # New Year's Day 2023 was a Sunday
    assert date(2022, 6, 20) in us_market_holidays(2022)  # the first Juneteenth, a Sunday


def test_saturday_holidays_move_to_the_friday_before():
    assert us_market_holidays(2026)[date(2026, 7, 3)] == "Independence Day"
    assert us_market_holidays(2021)[date(2021, 12, 24)] == "Christmas Day"


def test_saturday_new_years_day_is_not_observed_at_all():
    """NYSE Rule 7.2's month/year-end exception: 31 December trades."""
    assert "New Year's Day" not in us_market_holidays(2022).values()  # 1 Jan 2022 was a Saturday
    assert is_us_trading_day(date(2021, 12, 31))
    assert "New Year's Day" not in us_market_holidays(2028).values()


def test_juneteenth_and_mlk_day_only_from_the_years_they_were_adopted():
    assert JUNETEENTH_FIRST_YEAR == 2022 and MLK_DAY_FIRST_YEAR == 1998
    assert is_us_trading_day(date(2021, 6, 18))  # would be "observed" under today's rule
    assert "Juneteenth" not in us_market_holidays(2021).values()
    assert "Martin Luther King Jr. Day" not in us_market_holidays(1997).values()
    assert "Martin Luther King Jr. Day" in us_market_holidays(1998).values()


def test_special_one_off_closures_are_included():
    assert not is_us_trading_day(date(2018, 12, 5))
    assert not is_us_trading_day(date(2025, 1, 9))
    assert "Carter" in us_market_holidays(2025)[date(2025, 1, 9)]


@pytest.mark.parametrize(
    "year, expected",
    [
        (2000, date(2000, 4, 23)),
        (2019, date(2019, 4, 21)),
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2028, date(2028, 4, 16)),
        (2038, date(2038, 4, 25)),  # the latest possible date
        (2285, date(2285, 3, 22)),  # the earliest possible date
    ],
)
def test_easter_computus(year, expected):
    assert easter_sunday(year) == expected


# ---------------------------------------------------------------- sessions


def test_session_boundaries_are_half_open():
    # Monday 2026-09-28 (EDT, UTC-4): 09:30 ET = 13:30 UTC, 16:00 ET = 20:00 UTC
    assert not is_us_market_open(utc(2026, 9, 28, 13, 29, 59))
    assert is_us_market_open(utc(2026, 9, 28, 13, 30))
    assert is_us_market_open(utc(2026, 9, 28, 19, 59, 59))
    assert not is_us_market_open(utc(2026, 9, 28, 20, 0))


def test_session_follows_daylight_saving_time():
    # January is EST (UTC-5): 09:30 ET is 14:30 UTC, and 13:30 UTC is pre-market.
    assert not is_us_market_open(utc(2027, 1, 12, 14, 0))
    assert is_us_market_open(utc(2027, 1, 12, 14, 30))


def test_a_naive_datetime_is_read_as_utc_not_local_time():
    """Every stored timestamp is naive UTC; astimezone() would read a naive
    value as the server's local time instead."""
    assert to_market_time(datetime(2026, 9, 28, 13, 30)) == to_market_time(utc(2026, 9, 28, 13, 30))
    assert is_us_market_open(datetime(2026, 9, 28, 13, 30))


def test_market_is_shut_all_day_on_a_holiday_and_on_weekends():
    assert not is_us_market_open(utc(2026, 11, 26, 16, 0))  # Thanksgiving, 11:00 ET
    assert not is_us_market_open(utc(2026, 9, 27, 16, 28))  # Sunday 12:28 ET: the F-6 bug's moment
    assert not is_us_market_open(utc(2026, 7, 3, 15, 0))  # Independence Day observed


def test_early_close_ends_the_session_and_the_grace_window_at_one_pm():
    # Friday 2026-11-27 (EST): 13:00 ET = 18:00 UTC
    assert is_us_market_open(utc(2026, 11, 27, 17, 59))
    assert not is_us_market_open(utc(2026, 11, 27, 18, 0))
    assert is_within_post_close_grace(utc(2026, 11, 27, 18, 15))
    assert not is_within_post_close_grace(utc(2026, 11, 27, 18, 31))
    assert not is_within_post_close_grace(utc(2026, 11, 27, 21, 15))  # 16:15 ET is not "just after" a 1 pm close


def test_post_close_grace_on_a_normal_day_and_never_on_a_holiday():
    assert is_within_post_close_grace(utc(2026, 9, 28, 20, 0))
    assert is_within_post_close_grace(utc(2026, 9, 28, 20, 30))
    assert not is_within_post_close_grace(utc(2026, 9, 28, 20, 31))
    assert not is_within_post_close_grace(utc(2026, 11, 26, 21, 10))  # Thanksgiving


def test_crypto_is_always_open():
    sunday = utc(2026, 9, 27, 16, 28)
    assert is_market_open_for("BTC-USD", sunday)
    assert is_market_open_for("eth-usd", utc(2026, 11, 26, 16, 0))
    assert not is_market_open_for("NVDA", sunday)


def test_next_open_skips_weekends_and_holidays():
    # Wednesday before Thanksgiving, after the close -> Friday's open
    assert next_us_open(utc(2026, 11, 25, 22, 0)) == to_market_time(utc(2026, 11, 27, 14, 30))
    # Friday after the close before Labor Day -> Tuesday
    assert next_us_open(utc(2026, 9, 4, 21, 0)) == to_market_time(utc(2026, 9, 8, 13, 30))
    # During a session, the next open is tomorrow's, not the one under way
    assert next_us_open(utc(2026, 9, 28, 15, 0)) == to_market_time(utc(2026, 9, 29, 13, 30))
    # Pre-market, it's today's
    assert next_us_open(utc(2026, 9, 28, 12, 0)) == to_market_time(utc(2026, 9, 28, 13, 30))


def test_next_close_is_early_close_aware():
    # Thanksgiving itself -> the 1:00 pm close the next day
    assert next_us_close(utc(2026, 11, 26, 16, 0)) == to_market_time(utc(2026, 11, 27, 18, 0))
    # Christmas Eve morning 2026 (a Thursday) -> 1:00 pm that day
    assert next_us_close(utc(2026, 12, 24, 15, 0)) == to_market_time(utc(2026, 12, 24, 18, 0))


def test_market_session_states():
    open_ = us_market_session(utc(2026, 9, 28, 15, 0))
    assert (open_.state, open_.is_open, open_.holiday_name) == ("open", True, None)
    assert open_.next_close == to_market_time(utc(2026, 9, 28, 20, 0))

    assert us_market_session(utc(2026, 9, 28, 12, 0)).state == "pre"
    assert us_market_session(utc(2026, 9, 28, 21, 0)).state == "after"

    weekend = us_market_session(utc(2026, 9, 27, 16, 28))
    assert (weekend.state, weekend.is_open, weekend.holiday_name) == ("closed", False, None)
    assert weekend.next_open == to_market_time(utc(2026, 9, 28, 13, 30))

    holiday = us_market_session(utc(2026, 11, 26, 16, 0))
    assert (holiday.state, holiday.holiday_name) == ("holiday", "Thanksgiving Day")
    assert holiday.next_close_is_early


def test_a_saturday_holiday_date_is_a_weekend_not_a_named_holiday():
    """4 July 2026 is a Saturday; the closure is Friday 3 July."""
    session = us_market_session(utc(2026, 7, 4, 16, 0))
    assert session.state == "closed" and session.holiday_name is None
    friday = us_market_session(utc(2026, 7, 3, 16, 0))
    assert (friday.state, friday.holiday_name) == ("holiday", "Independence Day")


def test_closure_reasons_and_time_format():
    assert us_closure_reason(utc(2026, 9, 27, 16, 28)) == "weekend"
    assert us_closure_reason(utc(2026, 11, 26, 16, 0)) == "Thanksgiving Day"
    assert us_closure_reason(utc(2026, 9, 28, 12, 0)) == "before the open"
    assert us_closure_reason(utc(2026, 9, 28, 21, 0)) == "after the close"
    assert us_closure_reason(utc(2026, 11, 27, 19, 0)) == "after the 1:00 pm early close"
    assert format_market_time(datetime(2026, 9, 28, 13, 45)) == "Mon 28 Sep 09:45 ET"
