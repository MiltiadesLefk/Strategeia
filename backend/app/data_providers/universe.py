from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
UNIVERSE_FILE = DATA_DIR / "sp500.csv"

# Temporary development-only narrowing: while set, every screen (Company
# dropdown, Market Scan, auto-scan) only sees these symbols, so testing
# doesn't burn time/rate-limit budget scanning the full bundled list.
# `sp500.csv` itself is untouched — set this to None to see the full
# universe again, nothing to restore.
DEV_TICKER_FILTER: list[str] | None = ["AAPL", "NVDA", "BTC-USD"]


@dataclass
class UniverseEntry:
    symbol: str
    name: str
    sector: str


@lru_cache
def load_universe() -> list[UniverseEntry]:
    with UNIVERSE_FILE.open(newline="", encoding="utf-8") as f:
        entries = [UniverseEntry(**row) for row in csv.DictReader(f)]
    if DEV_TICKER_FILTER:
        allowed = set(DEV_TICKER_FILTER)
        entries = [e for e in entries if e.symbol in allowed]
    return entries


def get_default_watchlist(n: int = 50) -> list[str]:
    universe = load_universe()
    return [entry.symbol for entry in universe[:n]]
