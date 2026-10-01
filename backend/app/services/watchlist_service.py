"""Rules for the watchlist you edit in Settings: what a valid list is, how a
typo is caught before saving, and how the saved list is described to the UI.

The file itself is `data_providers/universe_store.py`; how the layers combine
is `data_providers/universe.py`. Routers stay thin and call this.
"""

from __future__ import annotations

import logging

from app.config import AppSettings
from app.data_providers import universe, universe_store
from app.data_providers.base import DataProvider
from app.data_providers.universe import UniverseEntry, default_sector_for
from app.data_providers.universe_store import (
    MAX_WATCHLIST_SYMBOLS,
    MIN_WATCHLIST_SYMBOLS,
    SYMBOL_PATTERN,
    UNKNOWN_SECTOR,
    StoredSymbol,
)
from app.schemas.watchlist_schemas import SymbolCheckResponse, WatchlistEntry, WatchlistResponse

logger = logging.getLogger(__name__)

# A save looks up a company name for each NEW symbol that isn't in the bundled
# catalogue (one cached provider call each). Adding one ticker at a time is the
# normal case; this only bounds a pasted list of unknowns so a save can't turn
# into hundreds of sequential requests. Symbols past it are saved under their
# ticker as the name.
MAX_NAME_LOOKUPS_PER_SAVE = 25


class WatchlistError(ValueError):
    """The submitted list can't be saved; the message is shown to the user."""


def normalize_symbol(raw: str) -> str | None:
    """Upper-cased and trimmed, or None when it isn't a plausible ticker."""
    symbol = raw.strip().upper()
    return symbol if SYMBOL_PATTERN.match(symbol) else None


def normalize_symbols(raw: list[str]) -> list[str]:
    """Validate a submitted list: every symbol well-formed, duplicates dropped
    (first one wins, so order is kept), between the minimum and maximum size.
    Raises WatchlistError listing every bad symbol at once."""
    invalid: list[str] = []
    symbols: list[str] = []
    seen: set[str] = set()
    for item in raw:
        symbol = normalize_symbol(item)
        if symbol is None:
            invalid.append(item.strip() or "(blank)")
            continue
        if symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    if invalid:
        shown = ", ".join(repr(s) for s in invalid[:5]) + (" and more" if len(invalid) > 5 else "")
        raise WatchlistError(
            f"Not a valid symbol: {shown}. Use capital letters, digits and . - ^ only, up to 12 characters."
        )
    if len(symbols) < MIN_WATCHLIST_SYMBOLS:
        raise WatchlistError("The watchlist needs at least one symbol. Use Reset to go back to the bundled list.")
    if len(symbols) > MAX_WATCHLIST_SYMBOLS:
        raise WatchlistError(
            f"The watchlist can hold at most {MAX_WATCHLIST_SYMBOLS} symbols (you sent {len(symbols)})."
        )
    return symbols


def _company_name(symbol: str, provider: DataProvider) -> str | None:
    """Company name from the data provider, best effort (one cached call)."""
    try:
        name = (provider.get_company_overview(symbol).name or "").strip()
    except Exception as exc:  # noqa: BLE001 - a name is a nicety; never block adding a symbol over it
        logger.info("No company name for %s: %s", symbol, exc)
        return None
    # Some providers echo the ticker back when they have nothing better.
    return name if name and name.upper() != symbol else None


def check_symbol(raw: str, provider: DataProvider) -> SymbolCheckResponse:
    """Does this ticker really return a quote? Catches typos before they are
    saved. The quote doesn't need to be fresh (the provider cache is fine):
    this answers "does it exist", not "what is the price"."""
    symbol = normalize_symbol(raw)
    if symbol is None:
        return SymbolCheckResponse(
            symbol=raw.strip().upper(),
            valid=False,
            message="Not a valid symbol. Use capital letters, digits and . - ^ only, up to 12 characters.",
        )
    try:
        quote = provider.get_quote(symbol)
        priced = quote.price is not None and quote.price > 0
    except Exception as exc:  # noqa: BLE001 - any provider failure means "can't confirm it"
        logger.info("Quote check failed for %s: %s", symbol, exc)
        priced = False
    if not priced:
        return SymbolCheckResponse(
            symbol=symbol,
            valid=False,
            message=(
                f"No market data came back for {symbol}. Check the spelling "
                "(for example BRK-B, not BRK.B on Yahoo), or try again if the data providers are down."
            ),
        )
    catalogue = universe.load_bundled_universe()
    known = next((e for e in catalogue if e.symbol == symbol), None)
    if known is not None:
        name, sector, in_catalogue = known.name, known.sector, True
    else:
        name = _company_name(symbol, provider) or symbol
        sector, in_catalogue = default_sector_for(symbol), False
    return SymbolCheckResponse(
        symbol=symbol,
        valid=True,
        name=name,
        sector=sector,
        sector_known=sector != UNKNOWN_SECTOR,
        in_catalogue=in_catalogue,
    )


def save_watchlist(raw: list[str], provider: DataProvider) -> None:
    """Validate and persist the list. Names for symbols the bundled catalogue
    doesn't know are resolved here, once, and stored with the symbol."""
    symbols = normalize_symbols(raw)
    previous = universe_store.read_store().watchlist
    previous_by_symbol = {s.symbol: s for s in previous.symbols} if previous else {}
    catalogue = {e.symbol: e for e in universe.load_bundled_universe()}
    lookups = 0
    stored: list[StoredSymbol] = []
    for symbol in symbols:
        if symbol in catalogue:
            # Shown from the catalogue when read back; nothing to look up.
            stored.append(StoredSymbol(symbol, catalogue[symbol].name, catalogue[symbol].sector))
        elif symbol in previous_by_symbol:
            stored.append(previous_by_symbol[symbol])
        else:
            name = None
            if lookups < MAX_NAME_LOOKUPS_PER_SAVE:
                lookups += 1
                name = _company_name(symbol, provider)
            stored.append(StoredSymbol(symbol, name or symbol, default_sector_for(symbol)))
    universe_store.write_store(stored)


def reset_watchlist() -> bool:
    """Back to the bundled list. True when a saved list was removed."""
    return universe_store.delete_store()


def _entry(e: UniverseEntry) -> WatchlistEntry:
    return WatchlistEntry(symbol=e.symbol, name=e.name, sector=e.sector, sector_known=e.sector != UNKNOWN_SECTOR)


def describe_watchlist(settings: AppSettings) -> WatchlistResponse:
    layer = universe.active_layer()
    entries = universe.load_universe()
    custom = universe.load_custom_universe()
    result = universe_store.read_store()
    editable = custom if custom is not None else universe.load_bundled_universe()
    return WatchlistResponse(
        active_layer=layer,
        entries=[_entry(e) for e in entries],
        editable_entries=[_entry(e) for e in editable],
        has_custom=custom is not None,
        custom_updated_at=result.watchlist.updated_at if result.watchlist else None,
        custom_error=result.error,
        dev_filter=universe.dev_ticker_filter(),
        bundled_size=len(universe.load_bundled_universe()),
        min_symbols=MIN_WATCHLIST_SYMBOLS,
        max_symbols=MAX_WATCHLIST_SYMBOLS,
        scan_universe_size=settings.scan_universe_size,
        scanned_count=min(settings.scan_universe_size, len(entries)),
    )
