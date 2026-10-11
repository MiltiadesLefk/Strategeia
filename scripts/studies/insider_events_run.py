"""Test A step 4: events -> returns -> results tables. Read-only (reads runtime/insider_events.db and the price store, refresh='never')."""
import json, sys, sqlite3, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from insider_events_common import *
import numpy as np, pandas as pd
from app.data_providers.history_store import get_history_store

HOLDS = [(21, "1m"), (63, "3m"), (126, "6m"), (252, "12m")]
COST = 0.003
N_BASKETS = 1000


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
    filings, ev, maxd, cutoff = events_and_cutoff()
    print(f"latest filing date in data {maxd}; event cutoff {cutoff}; raw cluster events (2022-07-01..cutoff): {len(ev)}; distinct event tickers {len({e['symbol'] for e in ev})}")
    store = get_history_store()
    pool_info = json.loads((ROOT / "scratch" / "A_pool.json").read_text())
    pool_syms = pool_info["ok"]
    event_syms = sorted({e["symbol"] for e in ev})
    spy = bars(store, "SPY")
    cal = pd.DatetimeIndex(pd.to_datetime(spy["date"]))
    cols = sorted(set(event_syms) | set(pool_syms))
    O, C, V = {}, {}, {}
    nopx_events, nopx_pool = [], []
    for s in cols:
        b = bars(store, s)
        if b is None or len(b) == 0:
            (nopx_events if s in event_syms else nopx_pool).append(s)
            continue
        b = b.set_index(pd.to_datetime(b["date"]))
        O[s], C[s], V[s] = b["open"].reindex(cal), b["close"].reindex(cal), b["volume"].reindex(cal)
    O, C, V = pd.DataFrame(O), pd.DataFrame(C), pd.DataFrame(V)
    nopx_events = sorted(nopx_events)
    print(f"price columns loaded: {C.shape[1]} of {len(cols)} wanted; event tickers with no prices: {len(nopx_events)} of {len(event_syms)}; "
          f"pool tickers loaded {len([s for s in pool_syms if s in C.columns])} of {len(pool_syms)} (+{len(pool_info['bad'])} sampled tickers had no prices)")
    print("NO-PRICE EVENT TICKERS:", " ".join(nopx_events))
    dvol = (C * V).rolling(20, min_periods=20).mean().shift(1)
    liquid = (C >= 3) & (dvol >= 1_000_000)
    sma = C.rolling(200, min_periods=200).mean()
    mom = C.shift(21) / C.shift(126) - 1
    hist_ok = sma.notna() & mom.notna()
    gate = (C > sma) & (mom > 0) & hist_ok
    R = {h: (C.shift(-h) / O.shift(-1) - 1) for h, _ in HOLDS}
    poolcols = [s for s in pool_syms if s in C.columns]
    spyC = pd.Series(spy["close"].values, index=cal)
    spyO = pd.Series(spy["open"].values, index=cal)
    spyR = {h: (spyC.shift(-h) / spyO.shift(-1) - 1) for h, _ in HOLDS}
    sp500 = set(pd.read_csv(ROOT / "backend" / "data" / "sp500.csv")["symbol"].astype(str).str.upper())
    drops = {"no prices for ticker": 0, "no price at D": 0, "price < $3": 0, "dollar volume < $1M (or <20 bars)": 0}
    kept = []
    for e in ev:
        s = e["symbol"]
        j = int(cal.searchsorted(pd.Timestamp(e["date"]), side="right") - 1)
        e["j"] = j
        if s not in C.columns:
            drops["no prices for ticker"] += 1
            continue
        c = C[s].iloc[j]
        if np.isnan(c):
            drops["no price at D"] += 1
            continue
        if c < 3:
            drops["price < $3"] += 1
            continue
        if not bool(liquid[s].iloc[j]):
            drops["dollar volume < $1M (or <20 bars)"] += 1
            continue
        e["gate"] = bool(gate[s].iloc[j])
        e["hist"] = bool(hist_ok[s].iloc[j])
        e["in_sp500"] = s in sp500
        kept.append(e)
    print("DROPPED (sequential reasons):", drops, "| liquid events kept:", len(kept))
    print("liquid events without 200 bars of history (excluded from variant 2 only):", sum(1 for e in kept if not e["hist"]), "| passing gate:", sum(e["gate"] for e in kept))
    print("liquid events in today's S&P 500 list:", sum(e["in_sp500"] for e in kept), "| outside:", sum(not e["in_sp500"] for e in kept))
    cache = {}

    def day(j, h):
        k = (j, h)
        if k not in cache:
            lq = liquid[poolcols].iloc[j].values
            r = R[h][poolcols].iloc[j].values
            g = gate[poolcols].iloc[j].values
            ok = lq & ~np.isnan(r)
            arr = r[ok]
            garr = r[ok & g]
            cache[k] = (arr, float(arr.mean()) if len(arr) else np.nan, float(garr.mean()) if len(garr) else np.nan)
        return cache[k]

    def stats(evs, h, kind, rng):
        rows = []
        for e in evs:
            j = e["j"]
            sr = spyR[h].iloc[j]
            arr, pavg, gavg = day(j, h)
            own = R[h][e["symbol"]].iloc[j] if kind == "event" else gavg
            if np.isnan(own) or np.isnan(sr) or np.isnan(pavg):
                continue
            rows.append((e["date"].strftime("%Y-%m"), j, own, sr, pavg, gavg))
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["m", "j", "own", "spy", "pool", "gate"])
        mm = df.groupby("m")[["own", "spy", "pool", "gate"]].mean()
        diff = mm["own"] - mm["pool"]
        n = len(mm)
        half = 1.96 * diff.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
        pcts = []
        for m, sub in df.groupby("m"):
            sums = np.zeros(N_BASKETS)
            for j in sub["j"]:
                sums += rng.choice(day(int(j), h)[0], N_BASKETS)
            pcts.append(float(np.mean(sums / len(sub) < mm.loc[m, "own"])))
        return dict(n_months=n, n_events=len(df), own=mm["own"].mean(), spy=mm["spy"].mean(), pool=mm["pool"].mean(),
                    diff=diff.mean(), lo=diff.mean() - half, hi=diff.mean() + half, pct=100 * np.mean(pcts))

    def fmt(name, lab, s):
        if s is None:
            return f"{name} | {lab} | no data"
        return (f"{name} | {lab} | {s['n_months']} | {s['n_events']} | {100*s['own']:+.2f}% | {100*s['spy']:+.2f}% | {100*s['pool']:+.2f}% | "
                f"{100*s['diff']:+.2f}% ({100*s['lo']:+.2f}% to {100*s['hi']:+.2f}%) | {s['pct']:.1f}th")

    v1 = kept
    v2 = [e for e in kept if e["hist"] and e["gate"]]
    out = {}
    print("\nVariant | Hold | n months | n events | avg return | SPY avg | pool avg | diff vs pool (95%) | basket pctile")
    for name, evs, kind in [("V1 all liquid events", v1, "event"), ("V2 events + gate", v2, "event"), ("V3 gate-only control", v1, "gatectl")]:
        for h, lab in HOLDS:
            s = stats(evs, h, kind, np.random.default_rng(7))
            out[(name, h)] = s
            print(fmt(name, lab, s))
        print()
    print("Cost line (information): average return minus 0.3% round trip")
    for name in ["V1 all liquid events", "V2 events + gate"]:
        print(name, " | ".join(f"{lab}: {100*(out[(name, h)]['own']-COST):+.2f}%" for h, lab in HOLDS if out[(name, h)]))
    print("\nInfo only: V2 minus V3 (insider cluster + gate vs gate alone, same days)")
    for h, lab in HOLDS:
        rows = []
        for e in v2:
            gavg = day(e["j"], h)[2]
            r = R[h][e["symbol"]].iloc[e["j"]]
            if not (np.isnan(r) or np.isnan(gavg)):
                rows.append((e["date"].strftime("%Y-%m"), r - gavg))
        d = pd.DataFrame(rows, columns=["m", "d"]).groupby("m")["d"].mean()
        n = len(d)
        half = 1.96 * d.std(ddof=1) / np.sqrt(n)
        print(f"{lab}: n months {n}, V2 minus gate-pool {100*d.mean():+.2f}% ({100*(d.mean()-half):+.2f}% to {100*(d.mean()+half):+.2f}%)")
    print("\nInfo only: variant 1 split by today's S&P 500 membership")
    for tag, evs in [("in S&P 500 list", [e for e in v1 if e["in_sp500"]]), ("outside S&P 500 list", [e for e in v1 if not e["in_sp500"]])]:
        for h, lab in HOLDS:
            print(fmt(tag, lab, stats(evs, h, "event", np.random.default_rng(7))))
    print("\nSUCCESS BAR (3m and 6m: beats SPY and pool, basket pctile >= 65, interval above zero, >= 24 months):")
    for name in ["V1 all liquid events", "V2 events + gate", "V3 gate-only control"]:
        ok = True
        for h in (63, 126):
            s = out[(name, h)]
            c = s is not None and s["own"] > s["spy"] and s["own"] > s["pool"] and s["pct"] >= 65 and s["lo"] > 0 and s["n_months"] >= 24
            print(f"  {name} {h}: {bool(c)}")
            ok &= bool(c)
        print(f"  => {name}: {'HAS A SIGNAL' if ok else 'no evidence'}")
    idx = np.random.default_rng(7).choice(len(v1), 8, replace=False)
    con = sqlite3.connect(DB)
    print("\nSAMPLE EVENTS (seed 7, from variant 1):")
    for i in sorted(idx):
        e = v1[i]
        print(f"{e['symbol']} {e['date']} insiders={e['n_insiders']} total=${e['value']:,.0f} gate={e['gate']}")
        for a in e["accessions"]:
            o, v, d = con.execute("SELECT (SELECT group_concat(owner_name, '; ') FROM owners w WHERE w.accession=f.accession), "
                                  "(SELECT sum(shares*price) FROM purchases p WHERE p.accession=f.accession), f.filing_date FROM filings f WHERE f.accession=?", (a,)).fetchone()
            print(f"    {a} filed {d} owners: {o} purchase value ${v:,.0f}")
    print("\nCOVERAGE (quarter, form4 filings with valid symbol, with purchase, purchase rows, bad-symbol form4 filings)")
    for r in con.execute("SELECT * FROM load_log"):
        print(r)
    print(con.execute("SELECT count(*), count(distinct symbol), min(filing_date), max(filing_date) FROM filings").fetchone(), "= filings with purchases, distinct symbols, first, last")
    print(con.execute("SELECT count(*) FROM purchases").fetchone(), "purchase rows;", con.execute("SELECT count(distinct owner_cik) FROM owners").fetchone(), "distinct owner CIKs")


main()
