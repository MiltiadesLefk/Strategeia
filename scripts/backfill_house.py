"""Load House members' stock-trade reports (Periodic Transaction Reports) into the
dated-fact table, with the day each report was filed as the moment it became known.

    backend/.venv/Scripts/python scripts/backfill_house.py --year 2025 --since 2025-01-01
    backend/.venv/Scripts/python scripts/backfill_house.py --year 2025 --since 2025-01-01 --max-filings 50 --db some/other.db
    backend/.venv/Scripts/python scripts/backfill_house.py --year 2025 --report

Every row of every report becomes one fact: known_at is the filing date (end of
that day in New York), effective_at is the trade date. A member may file up to 45
days after trading, so reading by trade date would use information that did not
exist yet. Amounts are ranges, not exact figures. A scanned (image-only) report is
recorded as unreadable, never guessed.

Re-running is safe and resumable: reports already stored are skipped without a
request, downloaded PDFs are cached on disk, and a report that fails to download
is reported and retried next time. The House Clerk's site is a public service with
no stated limit: requests are held to one a second. Use --max-filings to load a
year in pieces.

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
    parser.add_argument("--year", type=int, required=True, help="the index year to read, e.g. 2025")
    parser.add_argument("--since", type=_parse_date, default=None, help="first filing date, YYYY-MM-DD (default: start of the year)")
    parser.add_argument("--until", type=_parse_date, default=None, help="last filing date (default: end of the year / today)")
    parser.add_argument("--max-filings", type=int, default=None, help="read at most this many new reports, then stop")
    parser.add_argument("--db", default=None, help="SQLite file to write (default: the app's database)")
    parser.add_argument("--report", action="store_true", help="only print what is stored; no network")
    args = parser.parse_args(argv)

    from sqlmodel import Session, SQLModel, create_engine

    from app.config import get_infra_settings
    from app.knowledge import FactKind, fact_stats
    from app.knowledge import models as _knowledge_models  # noqa: F401  registers the table
    from app.knowledge.congress_trades import CONGRESS_FILING_KIND

    db_file = Path(args.db) if args.db else get_infra_settings().db_file
    db_file.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine, tables=[_knowledge_models.KnownFact.__table__])
    print(f"database: {db_file}")

    failed = False
    with Session(engine) as session:
        if not args.report:
            from app.data_providers.base import DataProviderError
            from app.data_providers.house_disclosures import HouseBackfillProgress, backfill_house

            def show(p: HouseBackfillProgress) -> None:
                if p.filings_done == p.filings_total or p.filings_done % 10 == 0:
                    print(
                        f"  {p.filings_done}/{p.filings_total} reports, {p.rows_created} new rows, "
                        f"{p.skipped_existing} already stored, {p.errors} errors"
                    )

            try:
                report = backfill_house(
                    session, args.year, since=args.since, until=args.until, max_filings=args.max_filings, progress=show
                )
            except DataProviderError as exc:
                print(f"could not read the House Clerk's index for {args.year}: {exc}")
                return 1
            print(
                f"{report.filings_listed} trade reports listed, {report.filings_ingested} read, "
                f"{report.filings_skipped_existing} already stored, {report.filings_remaining} still to do, "
                f"{report.rows_created} rows created, {report.unreadable} unreadable (scanned), {report.partial} partly read"
            )
            for error in report.errors:
                print(f"  failed: {error}")
            failed = bool(report.errors)
        trades = fact_stats(session, FactKind.CONGRESS_TRADE)
        filings = fact_stats(session, CONGRESS_FILING_KIND)
        print(f"  {trades.count} trade rows from {filings.count} reports, public {trades.first_known_at} .. {trades.last_known_at}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
