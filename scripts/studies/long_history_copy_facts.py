"""Test B: copy the fundamentals_revenue facts out of runtime/picker_study.db (opened read-only) into runtime/longhist.db.

    backend/.venv/Scripts/python scripts/studies/long_history_copy_facts.py
Never writes to picker_study.db. Re-running replaces runtime/longhist.db.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "runtime" / "picker_study.db"
DST = ROOT / "runtime" / "longhist.db"


def main() -> int:
    src = sqlite3.connect(f"file:{SRC.as_posix()}?mode=ro", uri=True, timeout=60)
    ddl = [r[0] for r in src.execute("select sql from sqlite_master where tbl_name='knownfact' and sql is not null")]
    if DST.exists():
        DST.unlink()
    dst = sqlite3.connect(DST)
    for q in ddl:
        dst.execute(q)
    n = 0
    for attempt in range(10):
        try:
            rows = src.execute("select * from knownfact where kind='fundamentals_revenue'").fetchall()
            break
        except sqlite3.OperationalError as exc:  # locked: wait and retry
            print("retry:", exc)
            time.sleep(15)
    else:
        print("gave up: database stayed locked")
        return 1
    dst.executemany("insert into knownfact values (?,?,?,?,?,?,?,?,?,?,?)", rows)
    dst.commit()
    n = dst.execute("select count(*), count(distinct symbol), min(known_at), max(known_at) from knownfact").fetchone()
    print(f"copied fundamentals_revenue rows: {n[0]}, symbols {n[1]}, known_at {n[2]} .. {n[3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
