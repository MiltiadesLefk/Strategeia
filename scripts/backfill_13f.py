"""Load 13F fund holdings (and the funds' own 5% owner filings) into the dated-fact
table, with their true public time.

    backend/.venv/Scripts/python scripts/backfill_13f.py --quarters 4
    backend/.venv/Scripts/python scripts/backfill_13f.py --ciks 1067983 1336528 --quarters 8
    backend/.venv/Scripts/python scripts/backfill_13f.py --quarters 2 --db some/other.db
    backend/.venv/Scripts/python scripts/backfill_13f.py --report

Without --ciks the funds are the ones set in Settings (the built-in starting list
when none are set). Each security a fund held becomes one row whose known_at is the
moment SEC accepted the filing and whose effective_at is the quarter end; a quarter
is visible to a backtest only from the day it was filed, up to 45 days after the
quarter ended. Re-running is safe: filings already stored are skipped without a
request. A fund or filing that fails is reported and skipped; the exit code is 1
if any failed.

Requests go through the shared SEC client (the configured User-Agent, at most 5
requests per second, filings cached on disk).

--db chooses the SQLite file. Without it the app's own database is used
(runtime/strategeia.db, or whatever DB_PATH points at). Use --db with a scratch
file for experiments.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))
# Importing the app's config would otherwise generate an API secret and write it
# into runtime/ the first time. A script has no use for the API's auth.
os.environ.setdefault("ALLOW_UNAUTHENTICATED_API", "true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ciks", nargs="*", default=None, help="manager CIK numbers (default: the followed funds)")
    parser.add_argument("--quarters", type=int, default=4, help="how many of the latest quarters per fund (default 4)")
    parser.add_argument("--db", default=None, help="SQLite file to write (default: the app's database)")
    parser.add_argument("--report", action="store_true", help="only print what is stored; no network")
    args = parser.parse_args(argv)

    from sqlmodel import Session, SQLModel, create_engine

    from app.config import get_infra_settings, load_app_settings
    from app.data_providers import sec_13f
    from app.knowledge import FactKind, fact_stats
    from app.knowledge import models as _knowledge_models  # noqa: F401  registers the table

    db_file = Path(args.db) if args.db else get_infra_settings().db_file
    db_file.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine, tables=[_knowledge_models.KnownFact.__table__])
    print(f"database: {db_file}")

    ciks = sec_13f.followed_fund_ciks(args.ciks if args.ciks else load_app_settings().smart_money_followed_funds)
    failed = False
    with Session(engine) as session:
        if not args.report:
            report = sec_13f.backfill_13f(session, ciks, args.quarters, progress=lambda line: print("  " + line))
            print(
                f"{report.funds} funds, {report.filings_seen} filings listed, {report.filings_ingested} ingested, "
                f"{report.filings_skipped_existing} already stored, {report.holdings_created} holdings created"
            )
            for error in report.errors:
                print(f"  failed: {error}")
            failed = bool(report.errors)
        for kind in (FactKind.FUND_FILING, FactKind.FUND_HOLDING):
            stats = fact_stats(session, kind)
            print(f"  {kind:<13} {stats.count} rows stored, public {stats.first_known_at} .. {stats.last_known_at}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
