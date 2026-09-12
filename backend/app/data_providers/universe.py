from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
UNIVERSE_FILE = DATA_DIR / "sp500.csv"

def _dev_ticker_filter() -> list[str] | None:
    """Development-only narrowing, opt-in via the STRATEGEIA_DEV_TICKERS env
    var (comma-separated, e.g. "NVDA,BTC-USD"): while set, every screen
    (Company dropdown, Market Scan, auto-scan) only sees those symbols, so
    testing doesn't burn time/rate-limit budget on the full bundled list.

    Deliberately an env var rather than a module constant. This used to be a
    hardcoded ["NVDA", "BTC-USD"] committed on main, which silently narrowed
    the whole app to two tickers while the dashboard still reported "markets
    scanned" — a dev override that ships by default is indistinguishable
    from a bug. Unset (the default) means the full universe, always."""
    raw = os.getenv("STRATEGEIA_DEV_TICKERS", "").strip()
    if not raw:
        return None
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    return symbols or None


@dataclass
class UniverseEntry:
    symbol: str
    name: str
    sector: str


@lru_cache
def load_universe() -> list[UniverseEntry]:
    with UNIVERSE_FILE.open(newline="", encoding="utf-8") as f:
        entries = [UniverseEntry(**row) for row in csv.DictReader(f)]
    dev_filter = _dev_ticker_filter()
    if dev_filter:
        allowed = set(dev_filter)
        entries = [e for e in entries if e.symbol in allowed]
    return entries


@lru_cache
def _sector_by_symbol() -> dict[str, str]:
    # Built off the unfiltered CSV on purpose: a position's sector must
    # resolve even when a dev ticker filter is narrowing the tradeable list.
    with UNIVERSE_FILE.open(newline="", encoding="utf-8") as f:
        return {row["symbol"]: row["sector"] for row in csv.DictReader(f) if row.get("sector")}


def get_sector(symbol: str) -> str | None:
    """Sector for a symbol, or None when it isn't in the bundled universe
    (a crypto pair, an off-list ticker). Callers treat None as "no sector
    opinion" rather than lumping unknowns into one pseudo-sector — see
    PaperTradingEngine._check_sector_concentration."""
    return _sector_by_symbol().get(symbol.upper())


def get_default_watchlist(n: int = 50) -> list[str]:
    universe = load_universe()
    return [entry.symbol for entry in universe[:n]]
