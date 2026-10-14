"""Keeps every watchlist symbol's Form 4 history complete: a year back through today.

The live SEC watcher only sees new filings from the day it starts. This job closes the rest:
a symbol with no coverage row (new, or the app was just installed) gets `COVERAGE_DAYS` of history
loaded, and a covered symbol that hasn't been topped up today gets the days since its last pass, so
a stretch with the app down leaves no gap. The coverage row moves only after a clean load."""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from sqlmodel import Session

from app.data_providers.sec_form4 import backfill_insider_trades
from app.timeutil import utcnow_naive
from app.watchers.models import InsiderCoverage

logger = logging.getLogger(__name__)

COVERAGE_DAYS = 365
TOP_UP_OVERLAP_DAYS = 3  # re-read a few days already covered: filings can be published a day late
TICK_BUDGET_SECONDS = 8 * 60  # a tick stops starting new symbols after this (ticks are 10 minutes apart)


def _is_sec_registrant_symbol(symbol: str) -> bool:
    return bool(symbol) and not symbol.endswith("-USD") and not symbol.startswith("^")


def _pending(session: Session, symbols: list[str], today: date) -> list[tuple[str, date]]:
    """(symbol, load-from date) for each symbol that needs work, never-loaded ones first."""
    target = today - timedelta(days=COVERAGE_DAYS)
    fresh: list[tuple[str, date]] = []
    stale: list[tuple[str, date]] = []
    for symbol in symbols:
        row = session.get(InsiderCoverage, symbol)
        if row is None or row.covered_from > target + timedelta(days=1):
            fresh.append((symbol, target))
        elif row.covered_through < today:
            stale.append((symbol, row.covered_through - timedelta(days=TOP_UP_OVERLAP_DAYS)))
    return fresh + stale


def run_insider_coverage_tick(
    session: Session,
    symbols: list[str],
    *,
    today: date | None = None,
    client=None,
    clock=time.monotonic,
) -> int:
    """Load what is missing for as many symbols as the time budget allows; returns how many were done."""
    today = today or utcnow_naive().date()
    cleaned = list(dict.fromkeys(s.strip().upper() for s in symbols if _is_sec_registrant_symbol(s.strip().upper())))
    started = clock()
    done = 0
    for symbol, since in _pending(session, cleaned, today):
        if clock() - started > TICK_BUDGET_SECONDS:
            break
        report = backfill_insider_trades(session, [symbol], since, today, client=client)
        if report.errors:
            logger.warning("insider coverage: %s not complete: %s", symbol, report.errors[0])
            if "HTTP 403" in report.errors[0] or "HTTP 429" in report.errors[0]:
                break  # SEC turned us away: stop for this tick
            continue
        row = session.get(InsiderCoverage, symbol)
        if row is None:
            row = InsiderCoverage(symbol=symbol, covered_from=since, covered_through=today)
        else:
            row.covered_from = min(row.covered_from, since)
            row.covered_through = today
        session.add(row)
        session.commit()
        done += 1
    return done
