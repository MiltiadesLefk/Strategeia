"""Load insider trades (SEC Form 4) into the dated-fact table, with their true
public time.

    backend/.venv/Scripts/python scripts/backfill_insider_trades.py AAPL MSFT --since 2016-01-01
    backend/.venv/Scripts/python scripts/backfill_insider_trades.py AAPL --since 2024-01-01 --db some/other.db
    backend/.venv/Scripts/python scripts/backfill_insider_trades.py AAPL --since 2024-01-01 --report

Each transaction row becomes one fact whose known_at is the moment SEC accepted
the filing (the earliest a trader could have seen it) and whose effective_at is
the transaction date. Re-running is safe: filings already stored are skipped, and
anything that is not skipped is deduplicated by accession and row. A filing or a
symbol that fails is reported and skipped; the exit code is 1 if any failed.

Requests go through the shared SEC client: the app's configured User-Agent (set
SEC_EDGAR_USER_AGENT to one with your contact address), at most 5 requests per
second, and downloaded filings are cached on disk so a re-run costs nothing.

--db chooses the SQLite file. Without it the app's own database is used
(runtime/strategeia.db, or whatever DB_PATH points at). Use --db with a scratch
file for experiments.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
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


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+", help="tickers, e.g. AAPL MSFT")
    parser.add_argument("--since", type=_parse_date, required=True, help="first filing date, YYYY-MM-DD")
    parser.add_argument("--until", type=_parse_date, default=None, help="last filing date (default: today)")
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

    with Session(engine) as session:
        if not args.report:
            from app.data_providers.sec_form4 import BackfillProgress, backfill_insider_trades

            def show(p: BackfillProgress) -> None:
                if p.filings_done == p.filings_total or p.filings_done % 25 == 0:
                    print(
                        f"  {p.symbol:<6} {p.filings_done}/{p.filings_total} filings, "
                        f"{p.facts_created} new rows, {p.skipped_existing} already stored, {p.errors} errors"
                    )

            report = backfill_insider_trades(session, args.symbols, args.since, args.until, progress=show)
            print(
                f"{report.symbols} symbols, {report.filings_seen} filings listed, {report.filings_ingested} ingested, "
                f"{report.filings_skipped_existing} already stored, {report.facts_created} rows created"
            )
            for symbol in report.unknown_symbols:
                print(f"  not an SEC registrant (no CIK): {symbol}")
            for error in report.errors:
                print(f"  failed: {error}")
        for symbol in args.symbols:
            stats = fact_stats(session, FactKind.INSIDER_TRADE, symbol=symbol)
            print(f"  {symbol.upper():<6} {stats.count} rows stored, public {stats.first_known_at} .. {stats.last_known_at}")
    if args.report:
        return 0
    return 1 if (report.errors or report.unknown_symbols) else 0


if __name__ == "__main__":
    raise SystemExit(main())
