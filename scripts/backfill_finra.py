"""Backfill FINRA daily short-sale volume for your symbols into the app database.

FINRA posts one file per trading day for the whole market. This downloads the
days you ask for (each file once, kept in runtime/finra/), and stores only the
symbols you name as dated facts, so a backtest can later see "what was the short
volume on the day, as known then". Re-running is safe: days already stored are
not downloaded again, so an interrupted run just continues.

    backend/.venv/Scripts/python scripts/backfill_finra.py AAPL NVDA --days 120
    backend/.venv/Scripts/python scripts/backfill_finra.py --watchlist --days 250
    backend/.venv/Scripts/python scripts/backfill_finra.py AAPL --start 2026-01-01 --end 2026-06-30
    backend/.venv/Scripts/python scripts/backfill_finra.py AAPL --db path/to/other.db   # a scratch database

By default it writes to the same database the app uses (DB_PATH, or
runtime/strategeia.db). Pass --db to use another file. It never needs the API
password: it talks to the database directly.

Short-sale volume is not short interest, and the signal built on it is silent:
it is recorded on plans but does not change confidence.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.dont_write_bytecode = True

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# This script never serves requests; without this the settings loader would
# create API secret files in runtime/ just because the app config was imported.
os.environ.setdefault("ALLOW_UNAUTHENTICATED_API", "true")

DEFAULT_DAYS = 120


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill FINRA short-sale volume into the database.")
    parser.add_argument("symbols", nargs="*", help="tickers, e.g. AAPL NVDA")
    parser.add_argument("--watchlist", action="store_true", help="use the current scan list (watchlist)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"calendar days back from today (default {DEFAULT_DAYS})")
    parser.add_argument("--start", type=date.fromisoformat, help="first date, YYYY-MM-DD (overrides --days)")
    parser.add_argument("--end", type=date.fromisoformat, help="last date, YYYY-MM-DD (default today)")
    parser.add_argument("--db", help="database file to write (default: the app's own database)")
    parser.add_argument("--cache-dir", help="where FINRA files are kept (default: runtime/finra next to the database)")
    args = parser.parse_args(argv)

    if args.db:
        os.environ["DB_PATH"] = args.db

    from sqlmodel import Session

    from app.config import load_app_settings
    from app.data_providers import universe
    from app.data_providers.finra_provider import FinraProvider
    from app.database import create_db_and_tables, engine
    from app.signals.finra import ingest_finra_short_volume

    symbols = [s.upper() for s in args.symbols]
    if args.watchlist:
        size = load_app_settings().scan_universe_size
        symbols += [e.symbol for e in universe.load_universe()[:size]]
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        parser.error("name at least one symbol, or use --watchlist")
    if args.days < 1:
        parser.error("--days must be at least 1")

    end = args.end or date.today()
    start = args.start or end - timedelta(days=args.days)
    if start > end:
        parser.error("--start is after --end")

    create_db_and_tables()
    provider = FinraProvider(cache_dir=Path(args.cache_dir) if args.cache_dir else None)
    print(f"{len(symbols)} symbols, {start} .. {end}", flush=True)
    with Session(engine) as session:
        result = ingest_finra_short_volume(session, symbols, start, end, provider)
    print(
        f"checked {result.days_checked} weekdays: downloaded {result.days_fetched}, "
        f"no file {result.days_without_file}, facts created {result.facts_created}, "
        f"already stored {result.facts_existing}"
    )
    for error in result.errors:
        print(f"  error: {error}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
