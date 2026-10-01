"""Load the Federal Reserve's recent statements, speeches and testimony into the
dated-fact table, with their true public time.

    backend/.venv/Scripts/python scripts/backfill_fed.py
    backend/.venv/Scripts/python scripts/backfill_fed.py --db some/other.db
    backend/.venv/Scripts/python scripts/backfill_fed.py --report

Reads the Fed's three public RSS feeds (monetary policy releases, speeches,
testimony). Each lists only its newest ~15 items, so this reaches back a few
months at most; older history is not loaded. Each item becomes one fact whose
known_at is its publication time. Re-running is safe: items already stored are
counted, not stored again, and a feed that failed gets another try. The exit code
is 1 if any feed failed.

--db chooses the SQLite file. Without it the app's own database is used
(runtime/strategeia.db, or whatever DB_PATH points at). Use --db with a scratch
file for experiments. --report prints what is stored and makes no request.
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
    parser.add_argument("--db", default=None, help="SQLite file to write (default: the app's database)")
    parser.add_argument("--report", action="store_true", help="only print what is stored; no network")
    args = parser.parse_args(argv)

    from sqlmodel import Session, SQLModel, create_engine

    from app.config import get_infra_settings
    from app.knowledge import FactKind, fact_stats
    from app.knowledge import models as _knowledge_models  # noqa: F401  registers the table

    db_file = Path(args.db) if args.db else get_infra_settings().db_file
    db_file.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine, tables=[_knowledge_models.KnownFact.__table__])
    print(f"database: {db_file}")

    failed = False
    with Session(engine) as session:
        if not args.report:
            from app.data_providers.fed_feed import backfill_fed

            report = backfill_fed(session)
            print(
                f"{report.feeds_ok} feeds read, {report.items_seen} items listed, {report.created} new, "
                f"{report.already_stored} already stored"
            )
            for error in report.errors:
                print(f"  failed: {error}")
            failed = bool(report.errors)
        stats = fact_stats(session, FactKind.FED_SPEECH)
        print(f"  {stats.count} Fed items stored, public {stats.first_known_at} .. {stats.last_known_at}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
