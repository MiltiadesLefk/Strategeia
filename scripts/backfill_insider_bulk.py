"""Backfill insider buys and sells from SEC's quarterly Insider Transactions Data Sets.

    backend/.venv/Scripts/python scripts/backfill_insider_bulk.py --since 2026-07-01              # the effective universe
    backend/.venv/Scripts/python scripts/backfill_insider_bulk.py --since 2026-07-01 --symbols NVDA AAPL
    backend/.venv/Scripts/python scripts/backfill_insider_bulk.py --since 2026-07-01 --market-wide
    backend/.venv/Scripts/python scripts/backfill_insider_bulk.py --since 2026-07-01 --db scratch.db

One download per finished quarter covers every company, so this is the cheap way to give a new install or a
newly added symbol its history. The data set carries the filing date, not the acceptance time, so each row is
dated to the END of its filing day (never earlier than the truth). A quarter SEC has not published yet is
reported and skipped: the live watcher covers the recent days. Re-running is safe (stored filings are skipped).
Uses the shared rate-limited SEC client; run one downloader at a time.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("ALLOW_UNAUTHENTICATED_API", "true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", type=date.fromisoformat, required=True, help="first filing date, YYYY-MM-DD")
    parser.add_argument("--until", type=date.fromisoformat, default=None, help="last filing date (default: today)")
    parser.add_argument("--symbols", nargs="*", default=None, help="tickers (default: the effective universe)")
    parser.add_argument("--market-wide", action="store_true", help="store every company, not just the universe")
    parser.add_argument("--db", default=None, help="SQLite file to write (default: the app's database)")
    args = parser.parse_args(argv)

    from sqlmodel import Session, SQLModel, create_engine

    from app.config import get_infra_settings
    from app.data_providers.sec_insider_bulk import backfill_from_bulk
    from app.data_providers.universe import load_universe
    from app.knowledge import models as knowledge_models

    db_file = Path(args.db) if args.db else get_infra_settings().db_file
    db_file.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine, tables=[knowledge_models.KnownFact.__table__])
    symbols = None if args.market_wide else (args.symbols or [e.symbol for e in load_universe()])
    scope = "every company" if symbols is None else f"{len(symbols)} symbols"
    print(f"database: {db_file}; {scope}")
    failed = False
    with Session(engine) as session:
        for r in backfill_from_bulk(session, args.since, args.until or date.today(), symbols=symbols):
            print(
                f"  {r.quarter}: {r.filings_seen} filings seen, {r.filings_stored} stored, {r.rows_created} rows, "
                f"{r.skipped_existing} already stored, {r.skipped_symbol} skipped (other symbol / no ticker)"
            )
            for error in r.errors:
                print(f"    {error}")
                failed = failed or "no insider data set" not in error
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
