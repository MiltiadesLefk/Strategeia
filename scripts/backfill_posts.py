"""Load the newest posts of Donald Trump from an UNOFFICIAL archive feed into the
dated-fact table, with their post time.

    backend/.venv/Scripts/python scripts/backfill_posts.py
    backend/.venv/Scripts/python scripts/backfill_posts.py --db some/other.db
    backend/.venv/Scripts/python scripts/backfill_posts.py --report

The source is trumpstruth.org, a third-party archive (Truth Social itself refuses
scripts). The feed lists only its newest ~100 posts, a day or two at the current
pace, so this is a way to seed the table, not a history: older posts are not
loaded (his 2009-2021 tweets are a separate export and out of scope). Re-running is
safe. The exit code is 1 if the download failed.

--db chooses the SQLite file (default: the app's own database). --report prints
what is stored and makes no request.
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
            from app.data_providers.posts_feed import backfill_posts

            report = backfill_posts(session)
            print(f"{report.posts_seen} posts listed, {report.created} new, {report.already_stored} already stored")
            for error in report.errors:
                print(f"  failed: {error}")
            failed = bool(report.errors)
        stats = fact_stats(session, FactKind.POST)
        print(f"  {stats.count} posts stored, public {stats.first_known_at} .. {stats.last_known_at}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
