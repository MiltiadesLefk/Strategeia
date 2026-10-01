"""The list of symbols every screen, scan and auto-scan works from.

Three layers decide it, strongest first:

1. `STRATEGEIA_DEV_TICKERS` (env var, comma-separated): an explicit operator
   override. While it is set, the list is exactly those symbols and nothing
   you save in the app changes what runs. Deliberately the strongest layer:
   it is how a beta deployment is narrowed to a couple of tickers, and an
   override that a saved list could quietly undo would not be an override.
2. The watchlist saved from Settings (`runtime/universe.json`, see
   `universe_store.py`): the list you maintain yourself.
3. The bundled `data/sp500.csv`: the default, baked into the image.

The bundled CSV is also the catalogue of names and sectors for every symbol
it knows, whichever layer is active. Nothing here is cached except the CSV
itself, so a save takes effect on the next call, with no restart.
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from app.data_providers import universe_store
from app.data_providers.universe_store import UNKNOWN_SECTOR

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
UNIVERSE_FILE = DATA_DIR / "sp500.csv"

# Sector given to a crypto pair that is not in the bundled CSV, matching what
# the CSV itself says for BTC-USD / ETH-USD / SOL-USD.
CRYPTO_SECTOR = "Crypto"

ActiveLayer = Literal["dev_filter", "custom", "bundled"]


def dev_ticker_filter() -> list[str] | None:
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
    symbols = list(dict.fromkeys(s.strip().upper() for s in raw.split(",") if s.strip()))
    return symbols or None


# Older name, kept so anything patching or importing it keeps working.
_dev_ticker_filter = dev_ticker_filter


@dataclass
class UniverseEntry:
    symbol: str
    name: str
    sector: str


@lru_cache
def _bundled_entries() -> tuple[UniverseEntry, ...]:
    """The bundled CSV, in file order. The only cached part of this module."""
    with UNIVERSE_FILE.open(newline="", encoding="utf-8") as f:
        return tuple(
            UniverseEntry(
                symbol=row["symbol"].strip().upper(),
                name=(row.get("name") or row["symbol"]).strip(),
                sector=(row.get("sector") or UNKNOWN_SECTOR).strip(),
            )
            for row in csv.DictReader(f)
            if row.get("symbol", "").strip()
        )


@lru_cache
def _catalogue() -> dict[str, UniverseEntry]:
    return {e.symbol: e for e in _bundled_entries()}


def reset_universe_caches() -> None:
    """Forget the parsed CSV and the saved-list state (tests; also after editing the CSV in place)."""
    _bundled_entries.cache_clear()
    _catalogue.cache_clear()
    universe_store.reset_state()


def load_bundled_universe() -> list[UniverseEntry]:
    """The bundled list as shipped, ignoring every override."""
    return [UniverseEntry(e.symbol, e.name, e.sector) for e in _bundled_entries()]


def default_sector_for(symbol: str) -> str:
    """Best sector guess for a symbol with no catalogue entry: "Crypto" for a
    -USD pair, otherwise unknown."""
    return CRYPTO_SECTOR if symbol.endswith("-USD") else UNKNOWN_SECTOR


def _resolve_entry(symbol: str, stored: universe_store.StoredSymbol | None = None) -> UniverseEntry:
    """Catalogue first (it may be corrected later), then what was stored when
    the symbol was added, then the bare symbol."""
    known = _catalogue().get(symbol)
    if known is not None:
        return UniverseEntry(known.symbol, known.name, known.sector)
    if stored is not None:
        sector = stored.sector if stored.sector != UNKNOWN_SECTOR else default_sector_for(symbol)
        return UniverseEntry(symbol, stored.name, sector)
    return UniverseEntry(symbol, symbol, default_sector_for(symbol))


def active_layer() -> ActiveLayer:
    if dev_ticker_filter():
        return "dev_filter"
    return "custom" if universe_store.read_store().watchlist is not None else "bundled"


def load_custom_universe() -> list[UniverseEntry] | None:
    """The list saved from Settings, or None when none is saved (or the file is unusable)."""
    stored = universe_store.read_store().watchlist
    if stored is None:
        return None
    return [_resolve_entry(s.symbol, s) for s in stored.symbols]


def load_universe() -> list[UniverseEntry]:
    """The effective list, in order (order matters: scans take the first N and
    the dashboard's top setups come from that scan). See the module docstring
    for the layer precedence."""
    dev_filter = dev_ticker_filter()
    if dev_filter:
        stored = universe_store.read_store().watchlist
        stored_by_symbol = {s.symbol: s for s in stored.symbols} if stored else {}
        return [_resolve_entry(symbol, stored_by_symbol.get(symbol)) for symbol in dev_filter]
    custom = load_custom_universe()
    if custom is not None:
        return custom
    return load_bundled_universe()


def get_sector(symbol: str) -> str | None:
    """Sector for a symbol, or None when we don't know it (an off-list ticker,
    a symbol added to the watchlist with no sector). Callers treat None as "no
    sector opinion" rather than lumping unknowns into one pseudo-sector — see
    PaperTradingEngine._check_sector_concentration.

    Resolved from the bundled catalogue plus the saved watchlist, never from
    the effective list: a position's sector must still resolve after its
    symbol was removed from the watchlist or the dev filter narrowed it away."""
    symbol = symbol.upper()
    known = _catalogue().get(symbol)
    if known is not None:
        return known.sector if known.sector != UNKNOWN_SECTOR else None
    stored = universe_store.read_store().watchlist
    if stored is not None:
        for entry in stored.symbols:
            if entry.symbol == symbol:
                sector = entry.sector if entry.sector != UNKNOWN_SECTOR else default_sector_for(symbol)
                return sector if sector != UNKNOWN_SECTOR else None
    return default_sector_for(symbol) if symbol.endswith("-USD") else None


def get_default_watchlist(n: int = 50) -> list[str]:
    universe = load_universe()
    return [entry.symbol for entry in universe[:n]]
