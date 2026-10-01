"""Load annual revenue (SEC XBRL filings) into the dated-fact table, dated by filing day.

    backend/.venv/Scripts/python scripts/backfill_fundamentals.py AAPL MSFT
    backend/.venv/Scripts/python scripts/backfill_fundamentals.py AAPL --db some/other.db
    backend/.venv/Scripts/python scripts/backfill_fundamentals.py AAPL --report

Each annual revenue value reported in a 10-K becomes one fact whose known_at is the
end of the day the filing was made (the SEC data gives the filing date, not the
time) and whose effective_at is the last day of the fiscal year. A year restated in
a later filing is stored again under that later date, so a backtest sees the
original number until the restatement was public. Re-running is safe: values
already stored are skipped. A symbol that fails is reported and skipped; the exit
code is 1 if any failed or is not an SEC registrant.

Requests go through the shared SEC client: the app's configured User-Agent (set
SEC_EDGAR_USER_AGENT to one with your contact address) and at most 5 requests per
second. About three requests are made per symbol.

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
            from app.data_providers.sec_xbrl import backfill_fundamentals

            def show(symbol: str, done: int, total: int) -> None:
                print(f"  {done}/{total} {symbol}")

            report = backfill_fundamentals(session, args.symbols, progress=show)
            print(
                f"{report.symbols} symbols, {report.facts_created} values stored, "
                f"{report.facts_already_stored} already stored"
            )
            for symbol in report.unknown_symbols:
                print(f"  not an SEC registrant (no CIK): {symbol}")
            for symbol in report.no_revenue_symbols:
                print(f"  no annual revenue under any known tag: {symbol}")
            for error in report.errors:
                print(f"  failed: {error}")
            failed = bool(report.errors or report.unknown_symbols)
        for symbol in args.symbols:
            stats = fact_stats(session, FactKind.FUNDAMENTALS_REVENUE, symbol=symbol)
            print(f"  {symbol.upper():<6} {stats.count} values stored, public {stats.first_known_at} .. {stats.last_known_at}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
