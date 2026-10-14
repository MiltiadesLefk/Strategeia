"""STEP 0 of the portfolio builder: does buying what passes the quality gate beat SPY? Read-only; every number is printed here.

    backend/.venv/Scripts/python scripts/studies/hold_gate_vs_spy.py --db runtime/catalyst_study.db

Only the parts of the live gate (app/analysis/quality_gate.py) that exist as point-in-time history are tested: close above the
200-day average and revenue growth > 0 (two consecutive fiscal years known at the month-end). Profit and valuation need
net income and P/E history that is not stored, so they are NOT in this test. Variants: GATE (both), GATE + DIP (also 10% or more
under the 63-day high). Each month-end buys every stock that passes, equal weight, held 1/3/6/12 months from the next open.
Compared with SPY, the equal-weight average of the whole universe, and 1000 random baskets of the same size.
Survivors only (today's index list), daily bars, no costs: optimistic, as every study here is.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app.analysis.quality_gate import DIP_LOOKBACK, DIP_PCT, MA_DAYS  # noqa: E402
from app.backtest.calendar import trading_days  # noqa: E402
from app.backtest.data_provider import PriceBook  # noqa: E402
from app.catalyst.picker import read_parts  # noqa: E402
from app.data_providers.history_store import get_history_store  # noqa: E402

BENCH = "SPY"
HOLDS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
FIRST = date(2023, 1, 1)
N_BASKETS = 1000
SEED = 7
VARIANTS = ("GATE", "GATE + DIP")


def universe() -> list[str]:
    with open(ROOT / "backend" / "data" / "sp500.csv", newline="") as f:
        return [r["symbol"] for r in csv.DictReader(f) if not r["symbol"].endswith("-USD")]


def month_ends(start: date, end: date) -> list[date]:
    days = trading_days(start, end)
    return [d for i, d in enumerate(days) if i + 1 == len(days) or days[i + 1].month != d.month]


def passes(series, i: int, growth: float | None) -> tuple[bool, bool]:
    """(gate, gate + dip) at bar `i`, from prices known at that close and revenue growth known then."""
    if i is None or i < MA_DAYS or growth is None or growth <= 0:
        return False, False
    closes = series.closes
    if not closes[i] > closes[i - MA_DAYS + 1 : i + 1].mean():
        return False, False
    window = closes[i - DIP_LOOKBACK + 1 : i + 1]
    return True, bool(closes[i] <= window.max() * (1 - DIP_PCT / 100))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--end", type=date.fromisoformat, default=date.today())
    args = ap.parse_args()

    syms = universe()
    book = PriceBook.load(get_history_store(), syms + [BENCH], date(FIRST.year - 2, 1, 1), args.end)
    series = {s: book.series[s] for s in syms if s in book}
    spy = book.series[BENCH]
    print(f"universe: {len(syms)} in sp500.csv, {len(series)} with stored prices; SPY present: {BENCH in book}")
    engine = create_engine(f"sqlite:///{Path(args.db).resolve()}")
    rows = {(v, h): [] for v in VARIANTS for h in HOLDS}
    months = month_ends(FIRST, args.end)
    with Session(engine) as session:
        for m in months:
            parts = read_parts(session, series, m, ("earnings",))
            symbols = sorted(parts)
            chosen = {v: [] for v in VARIANTS}
            for sym in symbols:
                gate, dip = passes(series[sym], series[sym].bar_index_on(m), parts[sym]["quality"])
                if gate:
                    chosen["GATE"].append(sym)
                if dip:
                    chosen["GATE + DIP"].append(sym)
            i_spy = spy.bar_index_on(m)
            for h, hold in HOLDS.items():
                if i_spy is None or i_spy + hold >= len(spy):
                    continue
                rets = {}
                for sym in symbols:
                    s = series[sym]
                    i = s.bar_index_on(m)
                    if i is not None and i + hold < len(s):
                        rets[sym] = float(s.closes[i + hold] / s.opens[i + 1] - 1)
                if len(rets) < 50:
                    continue
                arr = np.array([rets[n] for n in sorted(rets)])
                spy_ret = float(spy.closes[i_spy + hold] / spy.opens[i_spy + 1] - 1)
                for v in VARIANTS:
                    pick = [s for s in chosen[v] if s in rets]
                    if not pick:
                        continue  # no picks this month: nothing bought, not counted
                    pr = float(np.mean([rets[s] for s in pick]))
                    rng = np.random.default_rng([SEED, m.toordinal(), hold, len(pick)])
                    idx = np.argsort(rng.random((N_BASKETS, len(arr))), axis=1)[:, : len(pick)]
                    rows[(v, h)].append({"pr": pr, "spy": spy_ret, "all": float(arr.mean()),
                                         "pct": float((arr[idx].mean(axis=1) < pr).mean()), "k": len(pick)})

    print(f"month-ends: {months[0]} .. {months[-1]} ({len(months)}); months with no pick are skipped")
    print("\nVariant | Hold | n | picks avg | SPY avg | universe avg | picks-SPY (95%) | beat universe | random-basket pctile | avg size")
    for v in VARIANTS:
        for h in HOLDS:
            r = rows[(v, h)]
            if not r:
                continue
            n = len(r)
            pr = np.array([x["pr"] for x in r]); sp = np.array([x["spy"] for x in r]); al = np.array([x["all"] for x in r])
            d = pr - sp
            half = 1.96 * d.std(ddof=1) / math.sqrt(n) if n > 1 else float("nan")
            print(
                f"{v} | {h} | {n} | {pr.mean():+.2%} | {sp.mean():+.2%} | {al.mean():+.2%} | "
                f"{d.mean():+.2%} ({d.mean()-half:+.2%} to {d.mean()+half:+.2%}) | {int((pr > al).sum())} of {n} | "
                f"{np.mean([x['pct'] for x in r])*100:.1f}th | {np.mean([x['k'] for x in r]):.1f}"
            )
        print()
    print("(95% intervals ignore overlap between the 3m/6m/12m windows, so they are too narrow; survivors only, no costs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
