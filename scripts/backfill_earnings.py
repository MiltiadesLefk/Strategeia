"""Load past earnings reports (EPS estimate, actual, surprise) into the dated-fact table.

    backend/.venv/Scripts/python scripts/backfill_earnings.py AAPL MSFT
    backend/.venv/Scripts/python scripts/backfill_earnings.py AAPL --rows 40 --db some/other.db
    backend/.venv/Scripts/python scripts/backfill_earnings.py AAPL --report

Each reported quarter becomes one fact, public from the END of its report day in
New York (the source does not say whether the company reported before the open or
after the close, so the later reading is used). Re-running is safe: quarters
already stored are skipped. A symbol that fails or has no history is reported and
skipped; the exit code is 1 if any failed.

Data comes from yfinance (the same call the live earnings history uses), so this
needs the network and is subject to Yahoo's rate limits: do a few symbols at a time.

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
    parser.add_argument("symbols", nargs="+", help="tickers, e.g. AAPL MSFT")
    parser.add_argument("--rows", type=int, default=None, help="rows to request per symbol (default 60, about 15 years)")
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
            from app.backtest.earnings_history import BACKFILL_ROWS, backfill_earnings

            def show(symbol: str, done: int, total: int) -> None:
                print(f"  {done}/{total} {symbol}")

            report = backfill_earnings(session, args.symbols, rows=args.rows or BACKFILL_ROWS, progress=show)
            print(
                f"{report.symbols} symbols, {report.facts_created} quarters stored, "
                f"{report.facts_already_stored} already stored"
            )
            for symbol in report.no_history_symbols:
                print(f"  no reported quarters found: {symbol}")
            for error in report.errors:
                print(f"  failed: {error}")
            failed = bool(report.errors)
        for symbol in args.symbols:
            stats = fact_stats(session, FactKind.EARNINGS_REPORT, symbol=symbol)
            print(f"  {symbol.upper():<6} {stats.count} quarters stored, public {stats.first_known_at} .. {stats.last_known_at}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
