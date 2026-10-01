"""Past earnings reports as dated facts, and the as-of reader for the backtester.

One `earnings_report` fact per reported quarter (EPS estimate, EPS actual, surprise
in percent), taken from yfinance's `get_earnings_dates` (the same call the live
earnings history uses, so a backtest and a live plan score the same numbers).

When is a report public?
------------------------
yfinance gives the report date and often a time of day, but the time is a scheduled
slot (sometimes a placeholder) and it does not say whether the company reported
before the open or after the close. So a report is treated as public only from the
END of its report day in New York. A before-the-open report is really public hours
earlier, and a trader could have used it that morning; this convention makes the
backtest see it a day late, never early. It can only hide a real surprise from the
score, not leak one.

`effective_at` is the report date as well: yfinance does not say which fiscal
quarter ended, and an invented quarter-end would be a guess.

What is NOT here: the date of the NEXT report. A date is announced weeks ahead, but
the data source has no announcement time, and a backtest may only use a date it can
show was already public. So the "earnings within three days" penalty is not
rebuilt: it stays unavailable in a backtest (see backtest/coverage.py), which makes
the backtest slightly more willing to trade into a report than the live app is.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd
from sqlmodel import Session

from app.data_providers.base import EarningsHistoryEntry
from app.knowledge import FactKind, end_of_local_day_utc, facts_known_as_of, make_dedupe_key, record_fact

logger = logging.getLogger(__name__)

SOURCE_NAME = "yfinance"
DEDUPE_PREFIX = "earnings"
# yfinance pages 25 rows at a time; 60 rows is about 15 years of quarters.
BACKFILL_ROWS = 60
# Same default as the live earnings history.
DEFAULT_HISTORY_LIMIT = 12

EarningsFrameFetcher = Callable[[str, int], "pd.DataFrame | None"]


def _yfinance_fetch(symbol: str, limit: int) -> pd.DataFrame | None:
    import yfinance as yf

    return yf.Ticker(symbol).get_earnings_dates(limit=limit)


def _number(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def parse_earnings_frame(frame: pd.DataFrame | None) -> list[EarningsHistoryEntry]:
    """Reported quarters only (a row with no "Reported EPS" has not happened yet),
    newest first."""
    if frame is None or frame.empty:
        return []
    entries: list[EarningsHistoryEntry] = []
    for ts, row in frame.iterrows():
        actual = _number(row.get("Reported EPS"))
        if actual is None:
            continue
        entries.append(
            EarningsHistoryEntry(
                date=pd.Timestamp(ts).date(),
                eps_estimate=_number(row.get("EPS Estimate")),
                eps_actual=actual,
                surprise_pct=_number(row.get("Surprise(%)")),
            )
        )
    entries.sort(key=lambda e: e.date, reverse=True)
    return entries


def ingest_earnings_reports(session: Session, symbol: str, entries: list[EarningsHistoryEntry]) -> tuple[int, int]:
    """Store reported quarters as dated facts. Returns (created, already_stored); safe to repeat."""
    created = existing = 0
    for entry in entries:
        result = record_fact(
            session,
            kind=FactKind.EARNINGS_REPORT,
            symbol=symbol,
            source=SOURCE_NAME,
            dedupe_key=make_dedupe_key(DEDUPE_PREFIX, symbol.upper(), entry.date.isoformat()),
            known_at=end_of_local_day_utc(entry.date),
            known_at_basis="derived",
            effective_at=datetime(entry.date.year, entry.date.month, entry.date.day),
            payload={
                "report_date": entry.date.isoformat(),
                "eps_estimate": entry.eps_estimate,
                "eps_actual": entry.eps_actual,
                "surprise_pct": entry.surprise_pct,
            },
        )
        if result.created:
            created += 1
        else:
            existing += 1
    return created, existing


@dataclass
class EarningsBackfillReport:
    symbols: int = 0
    facts_created: int = 0
    facts_already_stored: int = 0
    no_history_symbols: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def backfill_earnings(
    session: Session,
    symbols: list[str],
    *,
    rows: int = BACKFILL_ROWS,
    fetcher: EarningsFrameFetcher = _yfinance_fetch,
    progress: Callable[[str, int, int], None] | None = None,
) -> EarningsBackfillReport:
    """Download and store each symbol's reported quarters. A symbol that fails or
    has no history is listed in the report and never stops the others."""
    report = EarningsBackfillReport()
    for index, raw in enumerate(symbols, start=1):
        symbol = raw.strip().upper()
        report.symbols += 1
        try:
            entries = parse_earnings_frame(fetcher(symbol, rows))
            if not entries:
                report.no_history_symbols.append(symbol)
                continue
            created, existing = ingest_earnings_reports(session, symbol, entries)
            report.facts_created += created
            report.facts_already_stored += existing
        except Exception as exc:  # noqa: BLE001 - yfinance raises assorted errors; one symbol must not stop the rest
            logger.warning("earnings backfill failed for %s: %s", symbol, exc)
            report.errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
        finally:
            if progress is not None:
                progress(symbol, index, len(symbols))
    return report


def earnings_history_as_of(
    session: Session, symbol: str, as_of: datetime | None = None, limit: int = DEFAULT_HISTORY_LIMIT
) -> list[EarningsHistoryEntry]:
    """Reported quarters public at the cutoff, newest report first (the shape the
    live earnings history has). Empty when none was public."""
    entries: list[EarningsHistoryEntry] = []
    for fact in facts_known_as_of(session, FactKind.EARNINGS_REPORT, as_of=as_of, symbol=symbol):
        p = fact.payload or {}
        try:
            report_date = date.fromisoformat(p["report_date"])
        except (KeyError, TypeError, ValueError):
            continue
        entries.append(
            EarningsHistoryEntry(
                date=report_date,
                eps_estimate=p.get("eps_estimate"),
                eps_actual=p.get("eps_actual"),
                surprise_pct=p.get("surprise_pct"),
            )
        )
    entries.sort(key=lambda e: e.date, reverse=True)
    return entries[:limit]
