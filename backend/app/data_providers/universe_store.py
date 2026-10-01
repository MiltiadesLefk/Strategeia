"""Persistence for the watchlist you edit in the app (Settings -> Watchlist).

The bundled `data/sp500.csv` is baked into the Docker image, so changing the
list used to mean editing a file and rebuilding. The list you save in the app
lives in a small JSON file next to settings.json (`runtime/universe.json`),
inside the volume that already survives `docker compose up --build`.

This module is only the file: read it, write it, forget it. How the layers
combine (the dev-ticker env var over this saved list over the bundled CSV) is
decided in `universe.py`, and what counts as an acceptable list is decided in
`services/watchlist_service.py`.

File shape:

    {"version": 1, "updated_at": "2026-10-01T12:00:00Z",
     "symbols": [{"symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology"}, ...]}

`name` and `sector` are stored for each symbol so that reading the list never
needs the network; for symbols that are in the bundled CSV, the CSV wins when
they are shown (it is the catalogue and may be corrected later).

Writes are atomic (temp file in the same folder, fsync, `os.replace`), so a
crash or a full disk mid-save leaves the previous list intact instead of a
half-written file. A file that cannot be read is never fatal: the app falls
back to the bundled list, says so once in the log, and shows the reason in
Settings.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_infra_settings
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

FILE_FORMAT_VERSION = 1
# What a ticker may look like: letters/digits plus the punctuation real tickers
# use (BRK-B, BF.B, BTC-USD, ^VIX). Uppercase only: callers upper-case first.
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.\-^]{1,12}$")
# Same ceiling as the scan endpoint's per-request symbol cap
# (api/routers/scanner.py MAX_SCAN_SYMBOLS): a watchlist the scanner would
# refuse to take in one request is not a watchlist worth saving.
MAX_WATCHLIST_SYMBOLS = 200
MIN_WATCHLIST_SYMBOLS = 1
UNKNOWN_SECTOR = "Unknown"


def universe_file() -> Path:
    """Looked up on every call (not cached) so a test can point it at a tmp path."""
    return get_infra_settings().universe_file


@dataclass(frozen=True)
class StoredSymbol:
    symbol: str
    name: str
    sector: str


@dataclass(frozen=True)
class StoredWatchlist:
    symbols: list[StoredSymbol]
    updated_at: datetime | None


@dataclass(frozen=True)
class StoreReadResult:
    """What is on disk right now: a usable list, nothing, or a reason it could not be used."""

    watchlist: StoredWatchlist | None
    error: str | None = None


_lock = threading.RLock()
# (file signature, result). The signature is the file's (mtime_ns, size), so a
# save from another process or a hand edit is picked up on the next read
# without re-parsing the file on every call.
_cache: tuple[tuple[int, int] | None, StoreReadResult] | None = None
# The signature we last logged a read problem for, so a broken file produces
# one warning, not one per request.
_last_warned_signature: tuple[int, int] | None = None


def reset_state() -> None:
    """Forget everything held in this process (the next read goes to the file)."""
    global _cache, _last_warned_signature
    with _lock:
        _cache = None
        _last_warned_signature = None


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _parse(raw: str) -> StoredWatchlist:
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
        raise ValueError("expected an object with a 'symbols' list")
    symbols: list[StoredSymbol] = []
    seen: set[str] = set()
    for item in data["symbols"]:
        if isinstance(item, str):
            item = {"symbol": item}
        if not isinstance(item, dict) or not isinstance(item.get("symbol"), str):
            raise ValueError("every symbol entry needs a 'symbol' string")
        symbol = item["symbol"].strip().upper()
        if not SYMBOL_PATTERN.match(symbol):
            raise ValueError(f"{item['symbol']!r} is not a valid symbol")
        if symbol in seen:
            continue
        seen.add(symbol)
        name = item.get("name")
        sector = item.get("sector")
        symbols.append(
            StoredSymbol(
                symbol=symbol,
                name=name if isinstance(name, str) and name.strip() else symbol,
                sector=sector if isinstance(sector, str) and sector.strip() else UNKNOWN_SECTOR,
            )
        )
    if not symbols:
        raise ValueError("the saved list is empty")
    updated_at = None
    raw_updated = data.get("updated_at")
    if isinstance(raw_updated, str):
        try:
            parsed = datetime.fromisoformat(raw_updated.replace("Z", "+00:00"))
            # Stored as naive UTC everywhere else in the app.
            updated_at = parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            updated_at = None
    return StoredWatchlist(symbols=symbols, updated_at=updated_at)


def read_store() -> StoreReadResult:
    """The saved watchlist, or None when there isn't one, or an error string
    when the file exists but cannot be used. Never raises."""
    global _cache, _last_warned_signature
    with _lock:
        path = universe_file()
        signature = _signature(path)
        if _cache is not None and _cache[0] == signature:
            return _cache[1]
        if signature is None:
            result = StoreReadResult(watchlist=None)
        else:
            try:
                result = StoreReadResult(watchlist=_parse(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:  # json.JSONDecodeError is a ValueError
                message = f"{path.name} could not be read ({exc}); using the bundled list instead."
                result = StoreReadResult(watchlist=None, error=message)
                if _last_warned_signature != signature:
                    logger.warning("Watchlist file %s is unusable: %s", path, message)
                    _last_warned_signature = signature
        _cache = (signature, result)
        return result


def write_store(symbols: list[StoredSymbol], now: datetime | None = None) -> StoredWatchlist:
    """Atomically replace the saved list. Callers have already validated it."""
    updated_at = (now or utcnow_naive()).replace(microsecond=0)
    payload = {
        "version": FILE_FORMAT_VERSION,
        "updated_at": updated_at.isoformat() + "Z",
        "symbols": [{"symbol": s.symbol, "name": s.name, "sector": s.sector} for s in symbols],
    }
    with _lock:
        path = universe_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        reset_state()
    return StoredWatchlist(symbols=list(symbols), updated_at=updated_at)


def delete_store() -> bool:
    """Remove the saved list (back to the bundled one). True when a file was removed."""
    with _lock:
        path = universe_file()
        existed = path.exists()
        path.unlink(missing_ok=True)
        reset_state()
        return existed
