"""Cluster-buying event rule for Test A (research only, no trading).

Input: purchase records, one per (accession, transaction) with its filing date and the owners of that filing.
An event for a ticker on day D: >= MIN_INSIDERS distinct owner CIKs and >= MIN_VALUE total purchase value in filings made
public in the WINDOW_DAYS calendar days up to and including D; one event per ticker per DEDUP_DAYS. Only filings dated <= D are
read, so a purchase filed after D can never create an event on D.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

MIN_INSIDERS = 3
MIN_VALUE = 100_000.0
WINDOW_DAYS = 30
DEDUP_DAYS = 90


def find_cluster_events(filings: list[dict], min_date: date | None = None) -> list[dict]:
    """filings: dicts with symbol, filing_date (date), accession, value (float, summed purchase value of the filing),
    owners (iterable of owner CIK strings). Returns events sorted by (symbol, date) with the evidence."""
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for f in filings:
        by_symbol[f["symbol"]].append(f)
    events: list[dict] = []
    for sym, items in by_symbol.items():
        items.sort(key=lambda f: f["filing_date"])
        days = sorted({f["filing_date"] for f in items})
        last_event: date | None = None
        for d in days:
            if last_event is not None and d <= last_event + timedelta(days=DEDUP_DAYS):
                continue
            lo = d - timedelta(days=WINDOW_DAYS - 1)
            window = [f for f in items if lo <= f["filing_date"] <= d]
            owners = {o for f in window for o in f["owners"]}
            value = sum(f["value"] for f in window)
            if len(owners) >= MIN_INSIDERS and value >= MIN_VALUE:
                last_event = d
                if min_date is None or d >= min_date:
                    events.append({"symbol": sym, "date": d, "n_insiders": len(owners), "value": value,
                                   "accessions": [f["accession"] for f in window]})
    events.sort(key=lambda e: (e["symbol"], e["date"]))
    return events
