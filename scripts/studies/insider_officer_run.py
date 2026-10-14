"""Study 05: officer-only insider buying x look-back window. Rules: internal_knowledge/studies/05_insider_officer_window/plan.md.
Read-only: runtime/insider_events.db, backend/runtime/history.db (refresh='never'), scratch/A_pool.json."""
import json, sqlite3, sys, time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
import numpy as np, pandas as pd
from app.data_providers.history_store import get_history_store

DB = ROOT / "runtime" / "insider_events.db"
EVENT_START, MIN_VALUE, DEDUP_DAYS = date(2022, 7, 1), 100_000.0, 180
HOLDS = [(21, "1m"), (63, "3m"), (126, "6m"), (252, "12m")]
COST, N_BASKETS = 0.003, 1000
VARIANTS = [("A officer 90d", True, 90), ("B officer 180d", True, 180), ("C all 90d", False, 90), ("D all 180d", False, 180)]


def load():
    con = sqlite3.connect(DB)
    val = dict(con.execute("SELECT accession, sum(shares*price) FROM purchases GROUP BY accession"))
    off = {a for (a,) in con.execute("SELECT DISTINCT accession FROM owners WHERE relationship LIKE '%Officer%'")}
    fl = [{"accession": a, "date": date.fromisoformat(d), "symbol": s, "value": val.get(a, 0.0), "officer": a in off}
          for a, d, s in con.execute("SELECT accession, filing_date, symbol FROM filings")]
    maxd = date.fromisoformat(con.execute("SELECT max(filing_date) FROM filings").fetchone()[0])
    con.close()
    return fl, maxd


def events_for(filings, officer_only, window, cutoff):
    by = defaultdict(list)
    for f in filings:
        if (f["officer"] or not officer_only) and f["value"] > 0:
            by[f["symbol"]].append(f)
    out = []
    for sym, items in by.items():
        items.sort(key=lambda f: f["date"])
        last = None
        for d in sorted({f["date"] for f in items}):
            if last is not None and d <= last + timedelta(days=DEDUP_DAYS):
                continue
            lo = d - timedelta(days=window - 1)
            v = sum(f["value"] for f in items if lo <= f["date"] <= d)
            if v >= MIN_VALUE:
                last = d
                if EVENT_START <= d <= cutoff:
                    out.append({"symbol": sym, "date": d, "value": v})
    return out


def bars(store, sym):
    for _ in range(20):
        try:
            return store.get_daily_history(sym, start=date(2021, 6, 1), refresh="never")
        except Exception as e:
            if "locked" in str(e):
                time.sleep(3)
                continue
            return pd.DataFrame()
    return pd.DataFrame()


def main():
    filings, maxd = load()
    cutoff = date(maxd.year - (1 if maxd.month <= 6 else 0), (maxd.month - 6 - 1) % 12 + 1, min(maxd.day, 28))
    evs = {name: events_for(filings, off, w, cutoff) for name, off, w in VARIANTS}
    print(f"latest filing date {maxd}; event cutoff {cutoff}")
    for name, e in evs.items():
        print(f"raw events {name}: {len(e)} ({len({x['symbol'] for x in e})} tickers)")
    store = get_history_store()
    pool_syms = json.loads((ROOT / "scratch" / "A_pool.json").read_text())["ok"]
    event_syms = sorted({x["symbol"] for e in evs.values() for x in e})
    spy = bars(store, "SPY")
    cal = pd.DatetimeIndex(pd.to_datetime(spy["date"]))
    O, C, V = {}, {}, {}
    for s in sorted(set(event_syms) | set(pool_syms)):
        b = bars(store, s)
        if b is None or len(b) == 0:
            continue
        b = b.set_index(pd.to_datetime(b["date"]))
        O[s], C[s], V[s] = b["open"].reindex(cal), b["close"].reindex(cal), b["volume"].reindex(cal)
    O, C, V = pd.DataFrame(O), pd.DataFrame(C), pd.DataFrame(V)
    print(f"price columns loaded {C.shape[1]}; event tickers without prices: {len([s for s in event_syms if s not in C.columns])} of {len(event_syms)}")
    dvol = (C * V).rolling(20, min_periods=20).mean().shift(1)
    liquid = (C >= 3) & (dvol >= 1_000_000)
    R = {h: (C.shift(-h) / O.shift(-1) - 1) for h, _ in HOLDS}
    poolcols = [s for s in pool_syms if s in C.columns]
    spyC, spyO = pd.Series(spy["close"].values, index=cal), pd.Series(spy["open"].values, index=cal)
    spyR = {h: (spyC.shift(-h) / spyO.shift(-1) - 1) for h, _ in HOLDS}
    sp500 = set(pd.read_csv(ROOT / "backend" / "data" / "sp500.csv")["symbol"].astype(str).str.upper())
    cache = {}

    def pool_day(j, h):
        if (j, h) not in cache:
            r = R[h][poolcols].iloc[j].values
            ok = liquid[poolcols].iloc[j].values & ~np.isnan(r)
            arr = r[ok]
            cache[(j, h)] = (arr, float(arr.mean()) if len(arr) else np.nan)
        return cache[(j, h)]

    kept = {}
    for name, e in evs.items():
        k, drops = [], 0
        for x in e:
            s = x["symbol"]
            j = int(cal.searchsorted(pd.Timestamp(x["date"]), side="right") - 1)
            if s not in C.columns or np.isnan(C[s].iloc[j]) or not bool(liquid[s].iloc[j]):
                drops += 1
                continue
            k.append({**x, "j": j, "in_sp500": s in sp500})
        kept[name] = k
        print(f"{name}: liquid events kept {len(k)} (dropped {drops})")

    def stats(evlist, h):
        rows = []
        for x in evlist:
            j, sr = x["j"], spyR[h].iloc[x["j"]]
            own, (arr, pavg) = R[h][x["symbol"]].iloc[j], pool_day(j, h)
            if np.isnan(own) or np.isnan(sr) or np.isnan(pavg):
                continue
            rows.append((x["date"].strftime("%Y-%m"), j, own, sr, pavg))
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["m", "j", "own", "spy", "pool"])
        mm = df.groupby("m")[["own", "spy", "pool"]].mean()
        diff, n = mm["own"] - mm["pool"], len(mm)
        half = 1.96 * diff.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
        rng, pcts = np.random.default_rng(7), []
        for m, sub in df.groupby("m"):
            sums = np.zeros(N_BASKETS)
            for j in sub["j"]:
                sums += rng.choice(pool_day(int(j), h)[0], N_BASKETS)
            pcts.append(float(np.mean(sums / len(sub) < mm.loc[m, "own"])))
        return dict(n_months=n, n=len(df), own=mm["own"].mean(), spy=mm["spy"].mean(), pool=mm["pool"].mean(),
                    diff=diff.mean(), lo=diff.mean() - half, hi=diff.mean() + half, pct=100 * np.mean(pcts))

    def fmt(name, lab, s):
        if s is None:
            return f"{name} | {lab} | no data"
        return (f"{name} | {lab} | {s['n_months']} | {s['n']} | {100*s['own']:+.2f}% | {100*s['spy']:+.2f}% | {100*s['pool']:+.2f}% | "
                f"{100*s['diff']:+.2f}% ({100*s['lo']:+.2f}% to {100*s['hi']:+.2f}%) | {s['pct']:.1f}th")

    out = {}
    print("\nVariant | Hold | n months | n events | avg return | SPY avg | pool avg | diff vs pool (95%) | basket pctile")
    for name, _, _ in VARIANTS:
        for h, lab in HOLDS:
            out[(name, h)] = stats(kept[name], h)
            print(fmt(name, lab, out[(name, h)]))
        print()
    print("Cost line (information): average return minus 0.3% round trip")
    for name, _, _ in VARIANTS:
        print(name, " | ".join(f"{lab}: {100*(out[(name, h)]['own']-COST):+.2f}%" for h, lab in HOLDS if out[(name, h)]))
    print("\nInfo only: officers minus all insiders, same window (difference of the two variants' average returns, no interval)")
    for a, c in [("A officer 90d", "C all 90d"), ("B officer 180d", "D all 180d")]:
        for h, lab in HOLDS[1:3]:
            if out[(a, h)] and out[(c, h)]:
                print(f"{a} minus {c} {lab}: {100*(out[(a, h)]['own']-out[(c, h)]['own']):+.2f}%")
    print("\nInfo only: S&P 500 split")
    for name, _, _ in VARIANTS:
        for tag, sub in [("in S&P 500", [x for x in kept[name] if x["in_sp500"]]), ("outside", [x for x in kept[name] if not x["in_sp500"]])]:
            for h, lab in HOLDS[1:3]:
                print(fmt(f"{name} {tag}", lab, stats(sub, h)))
    print("\nSUCCESS BAR (3m and 6m: beats SPY and pool, basket pctile >= 65, interval above zero, >= 24 months):")
    for name, _, _ in VARIANTS:
        ok = True
        for h in (63, 126):
            s = out[(name, h)]
            c = s is not None and s["own"] > s["spy"] and s["own"] > s["pool"] and s["pct"] >= 65 and s["lo"] > 0 and s["n_months"] >= 24
            print(f"  {name} {h}: {bool(c)}")
            ok &= bool(c)
        print(f"  => {name}: {'HAS A SIGNAL' if ok else 'no evidence'}")
    sofi = [x for x in evs["B officer 180d"] if x["symbol"] == "SOFI"]
    print("\nSANITY: SOFI events in variant B:", sofi)


main()
