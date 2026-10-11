"""PICKER TEST v3 (rules were written down before running; kept privately). Read-only.

    backend/.venv/Scripts/python scripts/studies/picker_sp500_ablation_threshold.py --db runtime/picker_study.db
    backend/.venv/Scripts/python scripts/studies/picker_sp500_ablation_threshold.py --db runtime/picker_study.db --kinds earnings,congress,insider,filing_8k

Universe: every non-crypto symbol of backend/data/sp500.csv that has stored prices. Variants: score >= 0.80 on momentum only,
quality only, catalyst only, all three (ablation), and the threshold rule (all three >= 0.80, momentum > 0, revenue growth > 0
when known).
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

from app.backtest.calendar import trading_days  # noqa: E402
from app.backtest.data_provider import PriceBook  # noqa: E402
from app.catalyst.picker import read_parts, score_stocks  # noqa: E402
from app.data_providers.history_store import get_history_store  # noqa: E402

BENCH = "SPY"
HOLDS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
FIRST = date(2023, 1, 1)
THRESHOLD = 0.80
N_BASKETS = 1000
SEED = 7
VARIANTS = ("momentum only", "quality only", "catalyst only", "all three (>=0.80)", "THRESHOLD RULE")


def universe() -> list[str]:
    with open(ROOT / "backend" / "data" / "sp500.csv", newline="") as f:
        return [r["symbol"] for r in csv.DictReader(f) if not r["symbol"].endswith("-USD")]


def month_ends(start: date, end: date) -> list[date]:
    days = trading_days(start, end)
    return [d for i, d in enumerate(days) if i + 1 == len(days) or days[i + 1].month != d.month]


def select(variant: str, parts: dict, symbols: list[str]) -> list[str]:
    if variant == "THRESHOLD RULE":
        scores = {s.symbol: s for s in score_stocks(parts)}
        out = []
        for sym in symbols:
            sc = scores[sym]
            if sc.score is None or sc.score < THRESHOLD:
                continue
            mom, qual = parts[sym]["momentum"], parts[sym]["quality"]
            if mom is None or mom <= 0 or (qual is not None and qual <= 0):
                continue
            out.append(sym)
        return out
    use = {
        "momentum only": ("momentum",), "quality only": ("quality",), "catalyst only": ("catalyst",),
        "all three (>=0.80)": ("quality", "momentum", "catalyst"),
    }[variant]
    return [s.symbol for s in score_stocks(parts, use) if s.score is not None and s.score >= THRESHOLD]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--kinds", default="earnings,congress", help="catalyst kinds to count")
    ap.add_argument("--end", type=date.fromisoformat, default=date.today())
    args = ap.parse_args()
    kinds = tuple(args.kinds.split(","))

    syms = universe()
    book = PriceBook.load(get_history_store(), syms + [BENCH], FIRST, args.end)
    series = {s: book.series[s] for s in syms if s in book}
    spy = book.series[BENCH]
    print(f"universe: {len(syms)} in sp500.csv, {len(series)} with stored prices; SPY present: {BENCH in book}")
    print(f"catalyst kinds counted: {kinds}; threshold {THRESHOLD}; months from {FIRST}")
    engine = create_engine(f"sqlite:///{Path(args.db).resolve()}")

    rows: dict[tuple[str, str], list[dict]] = {(v, h): [] for v in VARIANTS for h in HOLDS}
    cash: dict[str, int] = {v: 0 for v in VARIANTS}
    sizes: dict[str, list[int]] = {v: [] for v in VARIANTS}
    months = month_ends(FIRST, args.end)
    with Session(engine) as session:
        for m in months:
            parts = read_parts(session, series, m, kinds)
            symbols = sorted(parts)
            chosen = {v: select(v, parts, symbols) for v in VARIANTS}
            for v in VARIANTS:
                sizes[v].append(len(chosen[v]))
                cash[v] += not chosen[v]
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
                names = sorted(rets)
                arr = np.array([rets[n] for n in names])
                spy_ret = float(spy.closes[i_spy + hold] / spy.opens[i_spy + 1] - 1)
                for v in VARIANTS:
                    pick = [s for s in chosen[v] if s in rets]
                    if pick:
                        pr = float(np.mean([rets[s] for s in pick]))
                        rng = np.random.default_rng([SEED, m.toordinal(), hold, len(pick)])
                        idx = np.argsort(rng.random((N_BASKETS, len(arr))), axis=1)[:, : len(pick)]
                        pct = float((arr[idx].mean(axis=1) < pr).mean())
                    else:
                        pr, pct = 0.0, None  # cash
                    rows[(v, h)].append({"m": m, "pr": pr, "spy": spy_ret, "all": float(arr.mean()), "pct": pct, "k": len(pick)})

    print(f"\nmonth-ends scored: {months[0]} .. {months[-1]} ({len(months)})")
    print("\nVariant | Hold | n | picks avg | SPY avg | universe avg | picks-SPY (95%) | beat universe | basket pctile | avg size | cash months")
    for v in VARIANTS:
        for h in HOLDS:
            r = rows[(v, h)]
            if not r:
                continue
            n = len(r)
            pr = np.array([x["pr"] for x in r]); sp = np.array([x["spy"] for x in r]); al = np.array([x["all"] for x in r])
            d = pr - sp
            half = 1.96 * d.std(ddof=1) / math.sqrt(n) if n > 1 else float("nan")
            pcts = [x["pct"] for x in r if x["pct"] is not None]
            pct = f"{np.mean(pcts)*100:.1f}th" if pcts else "n/a"
            print(
                f"{v} | {h} | {n} | {pr.mean():+.2%} | {sp.mean():+.2%} | {al.mean():+.2%} | "
                f"{d.mean():+.2%} ({d.mean()-half:+.2%} to {d.mean()+half:+.2%}) | {int((pr > al).sum())} of {n} | {pct} | "
                f"{np.mean([x['k'] for x in r]):.1f} | {sum(1 for x in r if x['k']==0)}"
            )
        print()
    print("(95% intervals ignore overlap between 3m/6m/12m windows, so they are too narrow; 12m has very few independent months)")
    c = spy.closes
    j0 = int(np.searchsorted(spy.days, np.datetime64(FIRST)))
    dd = c[j0:] / np.maximum.accumulate(c[j0:]) - 1
    k = int(dd.argmin())
    print(f"SPY max drawdown from a peak in period: {dd[k]:.1%} on {np.datetime_as_string(spy.days[j0+k])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
