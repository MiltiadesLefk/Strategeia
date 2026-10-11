"""Test A step 3: load daily prices (history.db via the repo store) for event tickers and the seeded control pool.
Usage: python scripts/studies/insider_events_prices.py events | pool N | test20     (events: all event tickers; pool N: seeded order until N have prices)"""
import sys, time, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from insider_events_common import *
from app.data_providers.history_store import get_history_store

def load(store, syms, tag):
    ok, bad = [], []
    t0 = time.time()
    for i, s in enumerate(syms, 1):
        try:
            r = store.ensure_history([s], years=6, pace_seconds=0.2)[0]
            (ok if r.ok and store.coverage(s) is not None and (store.coverage(s).bar_count or 0) > 0 else bad).append(s)
        except Exception as e:
            bad.append(s)
        if i % 50 == 0:
            print(tag, i, "of", len(syms), "ok", len(ok), "bad", len(bad), f"{time.time()-t0:.0f}s", flush=True)
    return ok, bad

if __name__ == "__main__":
    mode = sys.argv[1]
    filings, ev, maxd, cutoff = events_and_cutoff()
    store = get_history_store()
    if mode == "test20":
        order = pool_order(filings)[:20]; t0 = time.time()
        ok, bad = load(store, order, "t20"); print("20 symbols", f"{time.time()-t0:.1f}s", "ok", len(ok), "bad", bad)
    elif mode == "events":
        syms = sorted({e["symbol"] for e in ev}); print("event tickers", len(syms), "events", len(ev), "cutoff", cutoff)
        ok, bad = load(store, syms, "ev"); print("DONE ok", len(ok), "bad", len(bad))
        Path(ROOT / "scratch" / "A_event_nopx.json").write_text(json.dumps(bad))
    elif mode == "pool":
        n = int(sys.argv[2]); order = pool_order(filings); ok, bad, i = [], [], 0
        t0 = time.time()
        for s in order:
            if len(ok) >= n: break
            o, b = load(store, [s], "pool"); ok += o; bad += b
            i += 1
            if i % 50 == 0: print("pool tried", i, "ok", len(ok), "bad", len(bad), f"{time.time()-t0:.0f}s", flush=True)
        print("DONE ok", len(ok), "bad", len(bad))
        Path(ROOT / "scratch" / "A_pool.json").write_text(json.dumps({"ok": ok, "bad": bad}))
