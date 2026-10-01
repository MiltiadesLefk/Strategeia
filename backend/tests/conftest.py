from __future__ import annotations

import os

# Module-level, not a fixture: this MUST run before `app.main` (and
# therefore `app.config.get_infra_settings()`) is ever imported, which
# happens at test-COLLECTION time (several test files do `from app.main
# import app` at module scope) — before any fixture, even an autouse one,
# gets a chance to run. pytest guarantees conftest.py in a directory loads
# before it imports any test module in that directory, which is exactly
# what makes module-level code here a safe place to do this.
#
# Without this, every router test (none of which send an X-API-Key header —
# they predate auth and were never meant to exercise it) would start
# getting 401s the moment get_infra_settings() auto-generates and requires
# a real shared secret by default (see config.py's
# _load_or_create_shared_secret). setdefault, not a flat assignment, so an
# explicit ALLOW_UNAUTHENTICATED_API in the real environment (e.g. someone
# deliberately testing the auth-required path end-to-end) is never
# clobbered. tests/test_api_validation.py's auth tests are unaffected
# either way — they monkeypatch app.api.deps.get_infra_settings directly
# with a fake, bypassing the real cached one entirely.
os.environ.setdefault("ALLOW_UNAUTHENTICATED_API", "true")
# Tests never read or write runtime/cache.db: the provider cache is memory-only
# unless a test builds its own store on a tmp path (test_persistent_cache.py).
# Assigned, not setdefault: a developer's own PERSIST_CACHE_DB=true must not
# make the suite touch their real cache file.
os.environ["PERSIST_CACHE_DB"] = "false"

import time
from datetime import datetime, timedelta

import pytest

# A known open US session: Tuesday 2026-09-29, 11:00 New York time (15:00
# UTC, naive like every stored timestamp). An ordinary trading day, mid-session,
# hours from either bell.
PINNED_OPEN_SESSION = datetime(2026, 9, 29, 15, 0)


@pytest.fixture(autouse=True)
def _engine_clock_in_an_open_session(monkeypatch):
    """PaperTradingEngine.open_position refuses to fill while the symbol's
    market is closed (plan.md F-6: on Sunday 2026-09-27 two auto-executed
    plans filled at Friday's close). Most tests that open a position predate
    that rule and assume a live market; on the real clock they would pass or
    fail depending on the weekday and hour the suite happens to run, and the
    auto-execute tests would quietly turn into "deferred to the open" tests
    every evening.

    So the rule stays strict and the clock is made deterministic instead: the
    engine's default clock is pinned to PINNED_OPEN_SESSION, ticking forward
    in real time from it so timestamps written in one test still come out in
    order. Only the DEFAULT clock is pinned: tests about the session gate
    itself (test_market_session_gate.py) pass their own `clock=` or re-patch
    this. Anything built on the real clock alone (app.markets called with no
    `now`, GET /api/market/session) is untouched."""
    started = time.monotonic()
    monkeypatch.setattr(
        "app.portfolio.engine.default_clock",
        lambda: PINNED_OPEN_SESSION + timedelta(seconds=time.monotonic() - started),
    )


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


@pytest.fixture(autouse=True)
def _isolated_watchlist_store(monkeypatch, tmp_path):
    """The watchlist saved from Settings is a file under runtime/ and the
    effective list is read from it on every call. No test may read or write the
    developer's real one: point the store at a tmp path that does not exist yet
    (so every test starts on the bundled list), forget the store's in-process
    state before and after, and drop STRATEGEIA_DEV_TICKERS so a developer's
    shell setting can't narrow a test that expects the full list. Tests about
    the precedence set it themselves with monkeypatch.setenv."""
    from app.data_providers import universe, universe_store

    monkeypatch.setattr(universe_store, "universe_file", lambda: tmp_path / "watchlist_runtime" / "universe.json")
    monkeypatch.delenv("STRATEGEIA_DEV_TICKERS", raising=False)
    universe.reset_universe_caches()
    yield
    universe.reset_universe_caches()


@pytest.fixture(autouse=True)
def _reset_in_process_cooldowns(monkeypatch):
    """Every in-process, not-persisted cooldown in the API (matching the
    established pattern — see scanner.py's AUTO_TRADE_COOLDOWN_SECONDS and
    settings.py's TEST_CONNECTION_COOLDOWN_SECONDS) lives in a module-level
    dict/variable that, by design, survives for the whole process — real
    protection against a tight request loop, but it also means the state
    persists ACROSS test functions in one pytest run. Without this,
    test_settings_router.py's three separate tests that each call
    target="telegram" once would fail from test 2 onward: the cooldown left
    by test 1 (run milliseconds earlier) would still be active and return
    429 instead of the real response each test asserts on.

    Reset before every test rather than fixed up in the affected tests
    individually, same reasoning as the macro-calendar fixture above: any
    future test/cooldown pair hits this the same way, not just the ones
    that already exist.
    """
    monkeypatch.setattr("app.api.routers.settings._last_test_connection_monotonic", {})
    monkeypatch.setattr("app.api.routers.scanner._last_auto_trade_run_monotonic", None)
    monkeypatch.setattr("app.api.routers.data_cache._last_cache_clear_monotonic", None)
    monkeypatch.setattr("app.api.routers.data_sources._last_probe_monotonic", {})
    # Data source health is process-wide too: one test's calls must not show in the next.
    from app.data_providers import health as _data_source_health

    _data_source_health.reset()
    monkeypatch.setattr("app.api.routers.missed_trades._last_missed_trades_refresh_monotonic", None)
    monkeypatch.setattr("app.api.routers.signals._last_finra_refresh_monotonic", None)
    monkeypatch.setattr("app.api.routers.news._last_news_collect_monotonic", None)
    monkeypatch.setattr("app.api.routers.news._last_news_label_monotonic", {})
    # PR Newswire feed cache and request pacing (data_providers/pr_newswire.py).
    from app.data_providers.pr_newswire import clear_feed_cache
    monkeypatch.setattr("app.api.routers.smart_money._last_insider_refresh_monotonic", None)
    monkeypatch.setattr("app.api.routers.portfolio._last_lesson_request_monotonic", {})
    monkeypatch.setattr("app.api.routers.watchers._last_watcher_run_monotonic", {})
    from app.services.market_terminal_service import reset_terminal_cache

    reset_terminal_cache()
    # The backtest start cooldown and the process-wide job manager (one run at a time).
    monkeypatch.setattr("app.api.routers.backtests._last_backtest_start_monotonic", None)
    from app.backtest.service import reset_backtest_manager

    reset_backtest_manager()
    # Same reasoning, different shape: app.auth.login_attempts is one
    # shared LoginAttemptTracker instance for the process (see its
    # docstring), so a lockout one test triggers on purpose would otherwise
    # still be counting down for the next test that logs in with the same
    # client key. Patched on api/routers/auth.py's own name for it, not
    # app.auth's — `from app.auth import login_attempts` there already
    # bound its own independent reference to the original object, which
    # patching app.auth's attribute would not reach.
    from app.auth import LoginAttemptTracker

    monkeypatch.setattr("app.api.routers.auth.login_attempts", LoginAttemptTracker())
