"""Shared helpers for Test A: events from runtime/insider_events.db (read-only), seeded control-pool order."""
import random, sqlite3, sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.catalyst.insider_events import find_cluster_events
DB = ROOT / "runtime" / "insider_events.db"
EVENT_START = date(2022, 7, 1)

def load_filings():
    con = sqlite3.connect(DB)
    val = dict(con.execute("SELECT accession, sum(shares*price) FROM purchases GROUP BY accession"))
    own = defaultdict(set)
    for a, o in con.execute("SELECT accession, owner_cik FROM owners"):
        own[a].add(o)
    out = []
    for a, d, s in con.execute("SELECT accession, filing_date, symbol FROM filings"):
        out.append({"accession": a, "filing_date": date.fromisoformat(d), "symbol": s, "value": val.get(a, 0.0), "owners": own.get(a, set())})
    maxd = con.execute("SELECT max(filing_date) FROM filings").fetchone()[0]
    con.close()
    return out, date.fromisoformat(maxd)

def events_and_cutoff():
    filings, maxd = load_filings()
    cutoff = date(maxd.year - (1 if maxd.month <= 6 else 0), (maxd.month - 6 - 1) % 12 + 1, min(maxd.day, 28))
    ev = [e for e in find_cluster_events(filings, EVENT_START) if e["date"] <= cutoff]
    return filings, ev, maxd, cutoff

def pool_order(filings):
    syms = sorted({f["symbol"] for f in filings if f["filing_date"] >= date(2022, 1, 1)})
    random.Random(7).shuffle(syms)
    return syms
