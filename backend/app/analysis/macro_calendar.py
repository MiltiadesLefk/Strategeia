from __future__ import annotations

from datetime import date, timedelta

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
# a live calendar feed (there isn't a free one). Verified 2026-09-13 directly
# against:
#   FOMC:  https://www.federalreserve.gov/newsevents/pressreleases/monetary20240809a.htm
#          (the Fed publishes ~18 months ahead; second day of each meeting is
#          the 2pm ET policy statement + press conference)
#   CPI:   https://www.bls.gov/schedule/2026/home.htm
#   NFP:   https://www.bls.gov/schedule/2026/home.htm ("Employment Situation")
# Re-verify and extend this list from those same three URLs whenever it runs
# short — each agency publishes its next year's calendar in the second half
# of the prior year.

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
    [("FOMC decision", d) for d in FOMC_DATES_2026]
    + [("CPI release", d) for d in CPI_RELEASE_DATES_2026]
    + [("jobs report", d) for d in NFP_RELEASE_DATES_2026]
)

# Same shape/cap as score_vix_regime: one-directional, small, never a bonus
# for "no event nearby" (that is just the normal state, not a signal).
MACRO_EVENT_SCORE_CAP = 1
# A stop set today can still be live tomorrow morning when the number drops —
# so "imminent" includes the day before, not just the day of.
MACRO_EVENT_LOOKAHEAD_DAYS = 1


def score_macro_event_proximity(today: date | None = None) -> tuple[int, list[str]]:
    """-MACRO_EVENT_SCORE_CAP when a scheduled macro release falls within
    MACRO_EVENT_LOOKAHEAD_DAYS, else 0. Symbol-independent by design — this
    is the same penalty for every plan generated on a given day, which is
    correct: the risk is to the whole tape, not to any one setup.

    Falls outside the hand-maintained table (a date beyond 2026, or before
    the app existed) contributes 0 rather than guessing — silence here means
    "not tracked," never "confirmed calm."
    """
    today = today or date.today()
    horizon_end = today + timedelta(days=MACRO_EVENT_LOOKAHEAD_DAYS)
    upcoming = [(label, d) for label, d in ALL_MACRO_EVENTS if today <= d <= horizon_end]
    if not upcoming:
        return 0, []

    label, event_date = min(upcoming, key=lambda pair: pair[1])
    when = "today" if event_date == today else "tomorrow"
    return -MACRO_EVENT_SCORE_CAP, [f"{label} {when} ({event_date.isoformat()}) — market-wide event risk"]
