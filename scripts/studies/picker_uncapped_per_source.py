"""PICKER TEST v4 (rules were written down before running; kept privately). Read-only.

    backend/.venv/Scripts/python scripts/studies/picker_uncapped_per_source.py --db runtime/picker_study.db

Uncapped catalyst points, each data source on its own, then combined, over holds of 1, 3, 6, 12 and 24 months.
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

from app.backtest.calendar import close_moment, trading_days  # noqa: E402
from app.backtest.data_provider import PriceBook  # noqa: E402
from app.catalyst.picker import catalyst_by_kind, momentum_value, quality_value, score_stocks  # noqa: E402
from app.catalyst.scoring import KIND_8K, KIND_CONGRESS, KIND_EARNINGS, KIND_INSIDER  # noqa: E402
from app.data_providers.history_store import get_history_store  # noqa: E402
from app.knowledge.point_in_time import as_of  # noqa: E402

BENCH = "SPY"
HOLDS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252, "24m": 504}
FIRST = date(2023, 1, 1)
THRESHOLD = 0.80
BIG_CAP = 1000
N_BASKETS = 1000
SEED = 7
VARIANTS = (
    "momentum (top fifth)", "quality (top fifth)",
    "insider buyers > 0", "congress net > 0", "congress net < 0 (avoid)",
    "8-K negative < 0 (avoid)", "earnings beat > 0", "earnings miss < 0 (avoid)",
    "ALL catalyst kinds summed (top fifth)", "ALL TOGETHER: quality+momentum+catalyst >= 0.80", "THRESHOLD RULE (uncapped)",
)


def universe() -> list[str]:
    with open(ROOT / "backend" / "data" / "sp500.csv", newline="") as f:
        return [r["symbol"] for r in csv.DictReader(f) if not r["symbol"].endswith("-USD")]


def month_ends(start: date, end: date) -> list[date]:
    days = trading_days(start, end)
    return [d for i, d in enumerate(days) if i + 1 == len(days) or days[i + 1].month != d.month]


def top_fifth(values: dict[str, float | None]) -> list[str]:
    """Symbols whose percentile rank among those having a value is >= THRESHOLD."""
    part = {s: {"momentum": v, "quality": None, "catalyst": None} for s, v in values.items() if v is not None}
    return [x.symbol for x in score_stocks(part, ("momentum",)) if x.score is not None and x.score >= THRESHOLD]


def choose(raw: dict[str, dict], symbols: list[str]) -> dict[str, list[str]]:
    k = {s: raw[s]["kinds"] for s in symbols}

    def val(kind, s):
        return k[s].get(kind)

    summed = {}
    for s in symbols:
        vs = [v for v in k[s].values() if v is not None]
        summed[s] = float(sum(vs)) if vs else None
    parts = {s: {"quality": raw[s]["quality"], "momentum": raw[s]["momentum"], "catalyst": summed[s]} for s in symbols}
    scored = {x.symbol: x for x in score_stocks(parts)}
    thr = []
    for s in symbols:
        sc = scored[s]
        mom, qual = parts[s]["momentum"], parts[s]["quality"]
        if sc.score is None or sc.score < THRESHOLD or mom is None or mom <= 0 or (qual is not None and qual <= 0):
            continue
        thr.append(s)
    return {
        "momentum (top fifth)": top_fifth({s: raw[s]["momentum"] for s in symbols}),
        "quality (top fifth)": top_fifth({s: raw[s]["quality"] for s in symbols}),
        "insider buyers > 0": [s for s in symbols if (val(KIND_INSIDER, s) or 0) > 0],
        "congress net > 0": [s for s in symbols if (val(KIND_CONGRESS, s) or 0) > 0],
        "congress net < 0 (avoid)": [s for s in symbols if (val(KIND_CONGRESS, s) or 0) < 0],
        "8-K negative < 0 (avoid)": [s for s in symbols if (val(KIND_8K, s) or 0) < 0],
        "earnings beat > 0": [s for s in symbols if (val(KIND_EARNINGS, s) or 0) > 0],
        "earnings miss < 0 (avoid)": [s for s in symbols if (val(KIND_EARNINGS, s) or 0) < 0],
        "ALL catalyst kinds summed (top fifth)": top_fifth(summed),
        "ALL TOGETHER: quality+momentum+catalyst >= 0.80": [
            s for s in symbols if scored[s].score is not None and scored[s].score >= THRESHOLD
        ],
        "THRESHOLD RULE (uncapped)": thr,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--end", type=date.fromisoformat, default=date.today())
    args = ap.parse_args()

    syms = universe()
    book = PriceBook.load(get_history_store(), syms + [BENCH], FIRST, args.end)
    series = {s: book.series[s] for s in syms if s in book}
    spy = book.series[BENCH]
    print(f"universe: {len(syms)} in sp500.csv, {len(series)} with stored prices; cap removed (kind_cap={BIG_CAP}); threshold {THRESHOLD}")
    engine = create_engine(f"sqlite:///{Path(args.db).resolve()}")
    rows: dict[tuple[str, str], list[dict]] = {(v, h): [] for v in VARIANTS for h in HOLDS}
    months = month_ends(FIRST, args.end)
    nonzero: dict[str, int] = {k: 0 for k in (KIND_INSIDER, KIND_CONGRESS, KIND_8K, KIND_EARNINGS)}
    with Session(engine) as session:
        for m in months:
            moment = close_moment(m)
            raw: dict[str, dict] = {}
            with as_of(moment):
                for sym, s in series.items():
                    i = s.bar_index_on(m)
                    if i is None:
                        continue
                    kinds = catalyst_by_kind(session, sym, moment, BIG_CAP)
                    for kk, vv in kinds.items():
                        nonzero[kk] += bool(vv)
                    raw[sym] = {"quality": quality_value(session, sym, moment), "momentum": momentum_value(s, i), "kinds": kinds}
            symbols = sorted(raw)
            chosen = choose(raw, symbols)
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
                    if not pick:
                        rows[(v, h)].append({"pr": None, "k": 0, "spy": spy_ret, "all": float(arr.mean()), "pct": None})
                        continue
                    pr = float(np.mean([rets[s] for s in pick]))
                    rng = np.random.default_rng([SEED, m.toordinal(), hold, len(pick)])
                    idx = np.argsort(rng.random((N_BASKETS, len(arr))), axis=1)[:, : len(pick)]
                    pct = float((arr[idx].mean(axis=1) < pr).mean())
                    rows[(v, h)].append({"pr": pr, "k": len(pick), "spy": spy_ret, "all": float(arr.mean()), "pct": pct})

    print(f"month-ends: {months[0]} .. {months[-1]} ({len(months)}); stock-months with a non-zero value per kind: {nonzero}")
    print("\nVariant | Hold | n months with picks (of months) | picks avg | SPY avg | universe avg | picks-universe (95%) | beat universe | basket pctile | avg size")
    for v in VARIANTS:
        for h in HOLDS:
            r = rows[(v, h)]
            used = [x for x in r if x["pr"] is not None]
            if len(used) < 2:
                print(f"{v} | {h} | {len(used)} of {len(r)} | too few months")
                continue
            n = len(used)
            pr = np.array([x["pr"] for x in used]); sp = np.array([x["spy"] for x in used]); al = np.array([x["all"] for x in used])
            d = pr - al
            half = 1.96 * d.std(ddof=1) / math.sqrt(n)
            print(
                f"{v} | {h} | {n} of {len(r)} | {pr.mean():+.2%} | {sp.mean():+.2%} | {al.mean():+.2%} | "
                f"{d.mean():+.2%} ({d.mean()-half:+.2%} to {d.mean()+half:+.2%}) | {int((pr > al).sum())} of {n} | "
                f"{np.mean([x['pct'] for x in used])*100:.1f}th | {np.mean([x['k'] for x in used]):.1f}"
            )
        print()
    print("(intervals ignore overlap between windows, so they are too narrow; 12m and 24m have very few independent months)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
