"""PICKER TEST v2 (rules were written down before running; kept privately). Read-only study; every number is printed by this script.

    backend/.venv/Scripts/python scripts/studies/picker_top6_60stocks.py --db runtime/picker_study.db
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app.backtest.calendar import trading_days  # noqa: E402
from app.backtest.data_provider import PriceBook  # noqa: E402
from app.catalyst.picker import TOP_N, score_month_end, top_picks  # noqa: E402
from app.data_providers.history_store import get_history_store  # noqa: E402

STOCKS = (
    "AAPL MSFT NVDA GOOGL AMZN META AVGO TSLA ORCL AMD JPM BAC GS V MA UNH LLY JNJ PFE MRK ABBV XOM CVX COP NEE DUK SO CEG VST "
    "LMT RTX NOC GD BA CAT DE HON GE UNP HD LOW COST WMT PG KO PEP MCD NKE DIS NFLX CRM ADBE PLTR CRWD PANW MU INTC T VZ LIN"
).split()
BENCH = "SPY"
HOLDS = {"1m": 21, "3m": 63, "6m": 126}
FIRST_MONTH_END_FROM = date(2023, 1, 1)
N_BASKETS = 1000
SEED = 7


def month_ends(start: date, end: date) -> list[date]:
    days = trading_days(start, end)
    return [d for i, d in enumerate(days) if i + 1 == len(days) or days[i + 1].month != d.month]


def coverage(db: Path) -> None:
    con = sqlite3.connect(db)
    print("DATA COVERAGE (kind | symbols with rows | rows | first known_at | last known_at)")
    for kind, syms, rows, lo, hi in con.execute(
        "select kind, count(distinct symbol), count(*), min(known_at), max(known_at) from knownfact group by kind order by kind"
    ):
        print(f"  {kind:<24} {syms:>4} {rows:>7}  {lo} .. {hi}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--end", type=date.fromisoformat, default=date.today())
    args = ap.parse_args()
    db = Path(args.db).resolve()
    coverage(db)

    book = PriceBook.load(get_history_store(), STOCKS + [BENCH], FIRST_MONTH_END_FROM, args.end)
    dropped = [s for s in STOCKS if s not in book]
    stocks = [s for s in STOCKS if s in book]
    print(f"\nstocks with price history: {len(stocks)} of {len(STOCKS)}; dropped: {dropped or 'none'}; SPY present: {BENCH in book}")
    series = {s: book.series[s] for s in stocks}
    spy = book.series[BENCH]
    first_bar = max(s.days[0] for s in series.values())
    print(f"price history from {np.datetime_as_string(first_bar)} (latest first bar among stocks) through {np.datetime_as_string(spy.days[-1])}")

    engine = create_engine(f"sqlite:///{db}")
    months = month_ends(FIRST_MONTH_END_FROM, args.end)
    rng = np.random.default_rng(SEED)
    results: dict[str, list[dict]] = {h: [] for h in HOLDS}
    flagged: dict[str, int] = {}
    with Session(engine) as session:
        for m in months:
            scores = score_month_end(session, series, m)
            picks = top_picks(scores, TOP_N)
            if not picks:
                continue
            for p in picks:
                if p.missing:
                    flagged[f"{p.symbol}:{','.join(p.missing)}"] = flagged.get(f"{p.symbol}:{','.join(p.missing)}", 0) + 1
            for label, hold in HOLDS.items():
                rets = {}
                ok = True
                idx_spy = spy.bar_index_on(m)
                if idx_spy is None or idx_spy + hold >= len(spy):
                    continue
                for sym, s in series.items():
                    i = s.bar_index_on(m)
                    if i is None or i + hold >= len(s):
                        continue
                    # entry: next trading day's open; exit: close of the bar `hold` bars after M
                    rets[sym] = float(s.closes[i + hold] / s.opens[i + 1] - 1)
                pick_rets = [rets[p.symbol] for p in picks if p.symbol in rets]
                if len(pick_rets) < len(picks):
                    continue
                spy_ret = float(spy.closes[idx_spy + hold] / spy.opens[idx_spy + 1] - 1)
                allv = np.array(list(rets.values()))
                pr = float(np.mean(pick_rets))
                baskets = np.array([rng.choice(allv, TOP_N, replace=False).mean() for _ in range(N_BASKETS)])
                results[label].append({
                    "m": m, "picks": [p.symbol for p in picks], "pr": pr, "spy": spy_ret, "all": float(allv.mean()),
                    "pct": float((baskets < pr).mean()), "end": spy.days[idx_spy + hold],
                })

    print("\nTOP-6 FLAGGED (stock:missing parts -> months picked while missing a part):", dict(sorted(flagged.items())) or "none")
    print(f"\nMonth-ends scored: {months[0]} .. {months[-1]} ({len(months)} month-ends)")
    print("\nHold | n | Picks avg | SPY avg | All-60 avg | Picks - SPY (95% interval) | beat all-60 | random-basket percentile (avg)")
    for label, rows in results.items():
        if not rows:
            print(f"{label} | 0 months")
            continue
        n = len(rows)
        pr = np.array([r["pr"] for r in rows]); sp = np.array([r["spy"] for r in rows]); al = np.array([r["all"] for r in rows])
        d = pr - sp
        half = 1.96 * d.std(ddof=1) / math.sqrt(n) if n > 1 else float("nan")
        print(
            f"{label} | {n} | {pr.mean():+.2%} | {sp.mean():+.2%} | {al.mean():+.2%} | {d.mean():+.2%} ({d.mean()-half:+.2%} to {d.mean()+half:+.2%}) "
            f"| {int((pr > al).sum())} of {n} | {np.mean([r['pct'] for r in rows])*100:.1f}th"
        )
        print(f"     first month {rows[0]['m']}, last month {rows[-1]['m']}")
    print("(interval is a plain 95% interval of monthly differences; overlapping 3m/6m windows make it too narrow)")

    # SPY regime
    c = spy.closes; days = spy.days
    start_i = int(np.searchsorted(days, np.datetime64(FIRST_MONTH_END_FROM)))
    peak = np.maximum.accumulate(c[start_i:]); dd = c[start_i:] / peak - 1
    j = int(dd.argmin())
    print(f"\nSPY in period: years {np.datetime_as_string(days[start_i])[:4]}..{np.datetime_as_string(days[-1])[:4]}; "
          f"max drawdown from a peak {dd[j]:.1%} on {np.datetime_as_string(days[start_i+j])}; fell >10%: {'yes' if dd[j] < -0.10 else 'no'}")
    for label, rows in results.items():
        if not rows:
            continue
        srt = sorted(rows, key=lambda r: r["pr"])
        print(f"\n{label}: 10 worst / 10 best month-ends (date, picks, picks return, SPY return)")
        for tag, part in (("worst", srt[:10]), ("best", srt[-10:][::-1])):
            for r in part:
                print(f"  {tag:<5} {r['m']} {' '.join(r['picks'])}  {r['pr']:+.2%}  spy {r['spy']:+.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
