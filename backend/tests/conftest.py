from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _stable_macro_calendar(monkeypatch):
    """`score_macro_event_proximity()` is called with no date argument
    throughout trade_plan_service.py and automation_service.py, so it reads
    the REAL `date.today()` — correct in production (see
    analysis/macro_calendar.py), but it makes every test that relies on a
    fixture's confidence margin over MIN_CONFIDENCE_FOR_TRADE non-deterministic:
    whenever the suite happens to run within a day of a real scheduled
    FOMC/CPI/jobs release, those tests lose a point they didn't expect and
    fail. Confirmed live: run on 2026-09-15, one day before the real
    2026-09-16 FOMC meeting, the unmocked -1 macro penalty (stacked with an
    unrelated -1 VIX-regime read) took the FakeUptrendDataProvider fixture
    below the confidence bar on BOTH the old 20-90 scale and the new 0-100
    one — so this is a pre-existing test-isolation gap, not something either
    scale introduced.

    Autouse and blanket rather than added to each affected test individually
    — the whole point is that any test exercising generate_trade_plan is
    exposed to this, not just the ones someone thought to check.
    automation_service.run_auto_scan only ever reaches this through
    generate_trade_plan (it has no macro-calendar import of its own), so
    patching the one module-level name here covers both call paths.
    tests/test_macro_calendar.py is unaffected: it imports and calls
    score_macro_event_proximity directly with explicit dates, never through
    trade_plan_service's module-level name patched here.
    """
    monkeypatch.setattr(
        "app.services.trade_plan_service.score_macro_event_proximity",
        lambda: (0, []),
    )
