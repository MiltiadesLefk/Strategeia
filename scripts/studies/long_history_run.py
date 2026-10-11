"""TEST B (rules were written down before running; kept privately). Read-only. Run from the repo root:

    backend/.venv/Scripts/python scripts/studies/long_history_run.py [--end YYYY-MM-DD]

Needs runtime/longhist_changes.csv (scripts/studies/long_history_members.py), runtime/longhist.db (scripts/studies/long_history_copy_facts.py)
and prices in backend/runtime/history.db (scripts/preload_history.py --years 17). Touches no network.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app.backtest.calendar import close_moment, trading_days  # noqa: E402
from app.backtest.data_provider import PriceBook  # noqa: E402
from app.catalyst.longhist import Change, members_on, month_row, norm_ticker, spy_above_200, top_by  # noqa: E402
from app.catalyst.picker import momentum_value, quality_value  # noqa: E402
from app.data_providers.history_store import get_history_store  # noqa: E402
from app.knowledge.point_in_time import as_of  # noqa: E402

BENCH = "SPY"
HOLDS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
FIRST = date(2011, 1, 1)
VARIANTS = ("V1 mom>=0.90", "V2 mom>=0.80", "V3 V1+rev>0", "V4 V1+SPY>200d", "V5 V3+SPY>200d")
COST = 0.003
MIN_BARS_INDEX = 126


def load_changes() -> list[Change]:
    out = []
    with open(ROOT / "runtime" / "longhist_changes.csv", newline="") as f:
        for r in csv.DictReader(f):
            out.append(Change(date.fromisoformat(r["day"]), r["added"] or None, r["removed"] or None))
    return out


def current() -> set[str]:
    with open(ROOT / "backend" / "data" / "sp500.csv", newline="") as f:
        return {norm_ticker(r["symbol"]) for r in csv.DictReader(f) if not r["symbol"].endswith("-USD")}


def month_ends(start: date, last_bar: date) -> list[date]:
    end = date(last_bar.year + 1, 1, 31)
    days = trading_days(start, end)
    out = [d for i, d in enumerate(days) if i + 1 == len(days) or days[i + 1].month != d.month]
    return [d for d in out if d <= last_bar]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", type=date.fromisoformat, default=date.today())
    args = ap.parse_args()

    cur, changes = current(), [c for c in load_changes() if c.day >= date(2010, 1, 1)]
    ever = set(cur)
    for c in changes:
        ever |= {x for x in (c.added, c.removed) if x}
    book = PriceBook.load(get_history_store(), sorted(ever) + [BENCH], date(2010, 1, 1), args.end)
    series = {s: book.series[s] for s in ever if s in book}
    spy = book.series[BENCH]
    no_price = sorted(ever - set(series))
    removed = ever - cur
    print(f"tickers ever members since 2010-01-01: {len(ever)}; with stored prices: {len(series)}; WITHOUT prices: {len(no_price)} "
          f"(of which removed tickers: {len(set(no_price) & removed)}, current-list tickers: {len(set(no_price) - removed)})")
    print(f"removed tickers (not in current list): {len(removed)}; of them with prices: {len(removed & set(series))}")
    print("no-price current-list tickers:", sorted(set(no_price) - removed))
    print(f"first 40 of the removed tickers without prices: {sorted(set(no_price) & removed)[:40]}")
    print(f"SPY bars {spy.days[0]} .. {spy.days[-1]} ({len(spy)})")

    months = month_ends(FIRST, spy.days[-1].astype("datetime64[D]").item())
    print(f"month-ends: {months[0]} .. {months[-1]} ({len(months)})")
    engine = create_engine(f"sqlite:///{(ROOT / 'runtime' / 'longhist.db').resolve()}")

    rows: dict[tuple[str, str], list[dict]] = {(v, h): [] for v in VARIANTS for h in HOLDS}
    early_exits = defaultdict(int)  # hold -> stock-months exited at a last close before the exit date
    stock_months = defaultdict(int)
    yearly_cov: dict[int, list[tuple[int, int, int]]] = defaultdict(list)  # year -> (members, tradeable, with revenue)
    v3_kept_missing: list[tuple[int, int]] = []
    month_info: dict[date, dict] = {}

    with Session(engine) as session:
        for m in months:
            moment = close_moment(m)
            members = members_on(cur, changes, m)
            i_spy = spy.bar_index_on(m)
            if i_spy is None or i_spy + 1 >= len(spy):
                continue
            filt = spy_above_200(spy.closes, i_spy)  # uses SPY closes up to and including M only
            raw: dict[str, dict] = {}
            with as_of(moment):
                for sym in members:
                    s = series.get(sym)
                    if s is None:
                        continue
                    i = s.bar_index_on(m)
                    if i is None or i < MIN_BARS_INDEX:
                        continue
                    raw[sym] = {"i": i, "mom": momentum_value(s, i), "q": quality_value(session, sym, moment)}
            syms = sorted(s for s in raw if raw[s]["mom"] is not None)
            yearly_cov[m.year].append((len(members), len(syms), sum(raw[s]["q"] is not None for s in syms)))
            v1 = top_by({s: raw[s]["mom"] for s in syms}, 0.90)
            v2 = top_by({s: raw[s]["mom"] for s in syms}, 0.80)
            v3 = [s for s in v1 if not (raw[s]["q"] is not None and raw[s]["q"] <= 0)]
            v3_kept_missing.append((sum(raw[s]["q"] is None for s in v3), len(v3)))
            chosen = {
                VARIANTS[0]: (v1, True), VARIANTS[1]: (v2, True), VARIANTS[2]: (v3, True),
                VARIANTS[3]: (v1, bool(filt)), VARIANTS[4]: (v3, bool(filt)),
            }
            month_info[m] = {"filter": filt, "n": len(syms)}
            d_entry = spy.days[i_spy + 1]
            for h, hold in HOLDS.items():
                if i_spy + hold >= len(spy):
                    continue
                d_exit = spy.days[i_spy + hold]
                spy_ret = float(spy.closes[i_spy + hold] / spy.opens[i_spy + 1] - 1)
                rets: dict[str, float] = {}
                for sym in syms:
                    s = series[sym]
                    j0 = int(np.searchsorted(s.days, d_entry, side="left"))
                    if j0 >= len(s) or s.days[j0] != d_entry:
                        continue
                    j1 = int(np.searchsorted(s.days, d_exit, side="right")) - 1
                    if j1 <= j0:
                        continue
                    stock_months[h] += 1
                    if s.days[j1] < d_exit - np.timedelta64(5, "D"):
                        early_exits[h] += 1
                    rets[sym] = float(s.closes[j1] / s.opens[j0] - 1)
                if len(rets) < 50:
                    continue
                arr = np.array([rets[k] for k in sorted(rets)])
                for v, (picks, invested) in chosen.items():
                    pr = [rets[s] for s in picks if s in rets]
                    r = month_row(pr, spy_ret, arr, invested, m, hold)
                    r["month"] = m
                    rows[(v, h)].append(r)

    # ---- coverage
    print("\nCOVERAGE per year (averages over that year's month-ends): members in the reconstructed index | tradeable (prices, >=127 bars) | with a revenue growth figure")
    for y in sorted(yearly_cov):
        a = np.array(yearly_cov[y], dtype=float).mean(axis=0)
        print(f"{y}: members {a[0]:.0f} | tradeable {a[1]:.0f} | with revenue figure {a[2]:.0f} ({a[2] / a[1]:.0%} of tradeable)")
    km = np.array(v3_kept_missing, dtype=float)
    print(f"V3: average basket {km[:, 1].mean():.1f} stocks, of which with NO revenue figure (kept): {km[:, 0].mean():.1f}")
    for h in HOLDS:
        print(f"stock-months with a return, hold {h}: {stock_months[h]}; exited at the last close before the exit date (stopped trading): {early_exits[h]}")
    nf = [m for m, x in month_info.items() if x["filter"] is False]
    print(f"months where SPY close <= its 200-day average (market filter says cash): {len(nf)} of {len(month_info)}")

    # ---- main table
    print("\nVariant | Hold | n months | picks avg | SPY avg | universe avg | picks-universe (95%) | beat SPY avg / universe avg | basket pctile (invested months) | avg size | cash months")
    stats: dict[tuple[str, str], dict] = {}
    for v in VARIANTS:
        for h in HOLDS:
            r = rows[(v, h)]
            if len(r) < 3:
                print(f"{v} | {h} | {len(r)} | too few months")
                continue
            n = len(r)
            pr = np.array([x["pr"] for x in r]); sp = np.array([x["spy"] for x in r]); al = np.array([x["all"] for x in r])
            d = pr - al
            half = 1.96 * d.std(ddof=1) / math.sqrt(n)
            inv = [x for x in r if not x["cash"]]
            pct = float(np.mean([x["pct"] for x in inv]) * 100) if inv else float("nan")
            cash = n - len(inv)
            stats[(v, h)] = dict(pr=pr.mean(), sp=sp.mean(), al=al.mean(), lo=d.mean() - half, pct=pct)
            print(f"{v} | {h} | {n} | {pr.mean():+.2%} | {sp.mean():+.2%} | {al.mean():+.2%} | {d.mean():+.2%} ({d.mean() - half:+.2%} to {d.mean() + half:+.2%}) | "
                  f"{'yes' if pr.mean() > sp.mean() else 'no'} / {'yes' if pr.mean() > al.mean() else 'no'} | {pct:.1f}th | "
                  f"{np.mean([x['k'] for x in inv]) if inv else 0:.1f} | {cash}")
            if h == "6m":
                pc = np.array([x["pr"] - (0 if x["cash"] else COST) for x in r])
                dc = pc - al
                print(f"{v} | 6m WITH 0.3% round-trip cost (info) | {n} | {pc.mean():+.2%} | SPY {sp.mean():+.2%} | universe {al.mean():+.2%} | picks-universe {dc.mean():+.2%}")
        print()
    print("(95% intervals ignore the overlap between windows, so they are too narrow, most of all for 6m and 12m)")

    # ---- per year
    spyc = spy.closes
    years = sorted({d.astype("datetime64[Y]").astype(int) + 1970 for d in spy.days})
    print("\nPER YEAR: SPY return (prior year-end close to this year-end close; last year to the latest bar) | SPY max drawdown inside the year | "
          "avg 6m-hold return of months STARTING that year: SPY, universe, V1, V3, V4 (n months)")
    bad_years: list[int] = []
    year_means: dict[tuple[str, int], float] = {}
    for y in years:
        if y < FIRST.year:
            continue
        idx = [k for k, d in enumerate(spy.days) if int(d.astype("datetime64[Y]").astype(int)) + 1970 == y]
        prev = [k for k, d in enumerate(spy.days) if int(d.astype("datetime64[Y]").astype(int)) + 1970 == y - 1]
        ret = spyc[idx[-1]] / spyc[prev[-1]] - 1 if prev else float("nan")
        run = np.maximum.accumulate(spyc[idx]); dd = float((spyc[idx] / run - 1).min())
        if dd < -0.10:
            bad_years.append(y)
        sel = {v: [x for x in rows[(v, "6m")] if x["month"].year == y] for v in VARIANTS}
        if not sel[VARIANTS[0]]:
            print(f"{y}: SPY {ret:+.1%} | max DD {dd:+.1%} | no month with a full 6m hold")
            continue
        n = len(sel[VARIANTS[0]])
        sp = np.mean([x["spy"] for x in sel[VARIANTS[0]]]); al = np.mean([x["all"] for x in sel[VARIANTS[0]]])
        for v in VARIANTS:
            year_means[(v, y)] = float(np.mean([x["pr"] for x in sel[v]]))
        year_means[("univ", y)] = float(al)
        print(f"{y}: SPY {ret:+.1%} | max DD {dd:+.1%} | 6m starts {n}: SPY {sp:+.1%}, universe {al:+.1%}, V1 {year_means[(VARIANTS[0], y)]:+.1%}, "
              f"V3 {year_means[(VARIANTS[2], y)]:+.1%}, V4 {year_means[(VARIANTS[3], y)]:+.1%}")
    print(f"years where SPY fell more than 10% from its peak inside the year: {bad_years}")

    # ---- worst months of V1
    r6 = sorted(rows[(VARIANTS[0], "6m")], key=lambda x: x["pr"])[:10]
    print("\nWORST 10 MONTHS of V1 by 6m return: month | stocks held | V1 | SPY | universe")
    for x in r6:
        print(f"{x['month']} | {x['k']} | {x['pr']:+.1%} | {x['spy']:+.1%} | {x['all']:+.1%}")

    # ---- success bar
    print("\nSUCCESS BAR per variant (6m AND 12m: beats SPY avg, beats universe avg, basket pctile >= 65, interval lower end > 0; and bad-year check on 6m)")
    for v in VARIANTS:
        ok = True
        for h in ("6m", "12m"):
            s = stats.get((v, h))
            if s is None:
                ok = False
                continue
            this = s["pr"] > s["sp"] and s["pr"] > s["al"] and s["pct"] >= 65 and s["lo"] > 0
            print(f"{v} {h}: beats SPY {s['pr'] > s['sp']}, beats universe {s['pr'] > s['al']}, pctile {s['pct']:.1f} (>=65 {s['pct'] >= 65}), interval lower end {s['lo']:+.2%} (>0 {s['lo'] > 0}) -> {this}")
            ok &= bool(this)
        chk = []
        for y in bad_years:
            if (v, y) in year_means:
                good = year_means[(v, y)] >= year_means[("univ", y)]
                chk.append(good)
                print(f"{v} bad year {y}: variant 6m avg {year_means[(v, y)]:+.1%} vs universe {year_means[('univ', y)]:+.1%} -> {'ok' if good else 'LOSES MORE'}")
        ok &= all(chk) and bool(chk)
        print(f"==> {v}: {'SURVIVES' if ok else 'no evidence'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
