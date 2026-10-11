"""Measure the catalyst idea on past days (plan.md section 8, step 1). Read-only on the app's data.

    backend/.venv/Scripts/python scripts/studies/catalyst_trigger_days.py VST NVDA --start 2025-03-01 --end 2026-10-07 --db runtime/catalyst_study.db

Facts come from a scratch database filled beforehand by the backfill scripts (--db on each of them); prices come
from runtime/history.db (scripts/preload_history.py). Nothing is written. The report gives the trigger days'
results next to random days of the same symbols with the same exits, and says what the data did not cover.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app.backtest.data_provider import PriceBook  # noqa: E402
from app.catalyst.study import StudyParams, run_study, summarize  # noqa: E402
from app.data_providers.history_store import get_history_store  # noqa: E402


def _fmt(summary) -> str:
    if summary.n == 0:
        return "no trades"
    ci = "" if summary.r_low is None else f" (95% {summary.r_low:+.2f} to {summary.r_high:+.2f})"
    return (
        f"n={summary.n}  win {summary.win_rate:.0%}  avg R {summary.avg_r:+.2f}{ci}  median R {summary.median_r:+.2f}  "
        f"jumped >=5% {summary.jump_rate:.0%}  exits {summary.exits}"
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="+")
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--db", required=True, help="SQLite file holding the dated facts")
    p.add_argument("--window", type=int, default=StudyParams.window_days)
    p.add_argument("--trigger", type=int, default=StudyParams.trigger)
    p.add_argument("--cap", type=int, default=StudyParams.kind_cap)
    p.add_argument("--list", action="store_true", help="print every trigger trade")
    p.add_argument("--events", action="store_true", help="print every day with a catalyst: exact items, rating, what followed")
    args = p.parse_args()

    symbols = [s.upper() for s in args.symbols]
    book = PriceBook.load(get_history_store(), symbols, args.start, args.end)
    engine = create_engine(f"sqlite:///{Path(args.db).resolve()}")
    params = StudyParams(window_days=args.window, trigger=args.trigger, kind_cap=args.cap)
    with Session(engine) as session:
        result = run_study(session, book, symbols, args.start, args.end, params)

    print(f"Catalyst study {args.start} .. {args.end}: {len(result.symbols)} symbols, {result.days_evaluated} symbol-days")
    print(f"  trigger: net >= {params.trigger} within {params.window_days} days, cap +-{params.kind_cap} per kind")
    print(f"  data coverage (symbols with any stored data): {result.coverage} of {len(result.symbols)}")
    print("  NOT covered: headline news, Trump posts/purchases, 13F/13D, Senate")
    if result.skipped:
        print(f"  skipped (no price history): {result.skipped}")
    print(f"  trigger days by kind: {result.kind_counts}")
    print("\nTRIGGERED  ", _fmt(summarize(result.triggers)))
    print("RANDOM DAYS", _fmt(summarize(result.baseline)))
    if args.list:
        for t in sorted(result.triggers, key=lambda t: t.day):
            print(f"  {t.day} {t.symbol:<5} net {t.net:+d} entry {t.entry:.2f} stop {t.stop:.2f} -> {t.reason} {t.r:+.2f}R | {t.detail}")
    if args.events:
        print("\nCATALYST LOG (open of the day -> close of day 1 / day 5 / day 10)")
        last = {}
        for e in sorted(result.events, key=lambda e: (e["symbol"], e["day"])):
            sig = tuple((i.kind, i.text) for i in e["items"])
            if last.get(e["symbol"]) == sig:
                continue  # the same item still inside its window: shown once
            last[e["symbol"]] = sig
            r = e["ret"]
            fmt = lambda v: "n/a" if v is None else f"{v:+.1f}%"
            print(f"\n{e['day']} {e['symbol']}  net {e['net']:+d}   next: {fmt(r[1])} / {fmt(r[5])} / {fmt(r[10])}")
            for i in e["items"]:
                print(f"   [{i.kind}] {i.points:+d}  {i.text}")
                print(f"        how rated: {i.why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
