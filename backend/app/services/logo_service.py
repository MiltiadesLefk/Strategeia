"""Company logos kept on disk (runtime/logos, in the same volume as the database) so the pages stop calling a third-party
API for every logo on every visit. `fetch_logo` downloads one (Elbstream for stocks, a crypto icon set for -USD pairs),
`sync_logos` fills the gaps for the whole universe, and `kick_sync` does that in the background right after a ticker joins it.

Logos are third-party files: only a real SVG without scripts or a real PNG is stored, size-capped, and the API serves them
with a sandboxing Content-Security-Policy. A ticker the source does not know is remembered for a week, not asked again."""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

import httpx

from app.config import get_infra_settings

logger = logging.getLogger(__name__)

STOCK_URL = "https://api.elbstream.com/logos/symbol/{symbol}"
CRYPTO_URL = "https://cdn.jsdelivr.net/gh/spothq/cryptocurrency-icons@master/128/color/{coin}.png"
MAX_BYTES = 200_000
TIMEOUT_SECONDS = 10
RETRY_MISSING_SECONDS = 7 * 24 * 3600
PAUSE_BETWEEN_DOWNLOADS = 0.25  # the source is free: no bursts
STOP_AFTER_FAILURES = 5  # in a row (a rate limit or an outage): try again at the next sync
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_sync_lock = threading.Lock()


def logo_dir() -> Path:
    return get_infra_settings().settings_file.parent / "logos"


def clean_symbol(raw: str) -> str | None:
    symbol = (raw or "").strip().upper()
    return symbol if SYMBOL_RE.match(symbol) else None


def cached_logo(symbol: str) -> Path | None:
    for ext in ("svg", "png"):
        path = logo_dir() / f"{symbol}.{ext}"
        if path.is_file():
            return path
    return None


def _url_for(symbol: str) -> str:
    if symbol.endswith("-USD"):
        return CRYPTO_URL.format(coin=symbol[: -len("-USD")].lower())
    return STOCK_URL.format(symbol=symbol)


def _extension_if_real_image(body: bytes) -> str | None:
    if body.startswith(PNG_SIGNATURE):
        return "png"
    head = body[:2000].lstrip().lower()
    if (head.startswith(b"<svg") or head.startswith(b"<?xml")) and b"<svg" in body[:4000].lower() and b"<script" not in body.lower():
        return "svg"
    return None


def fetch_logo(raw_symbol: str) -> str:
    """Download and store one logo. "stored", "have" (already on disk), "missing" (the source does not know it: noted for a
    week), "skipped" (a recent miss) or "error" (a network problem or a bad file: try again later)."""
    symbol = clean_symbol(raw_symbol)
    if symbol is None:
        return "error"
    if cached_logo(symbol) is not None:
        return "have"
    folder = logo_dir()
    marker = folder / f"{symbol}.missing"
    if marker.exists() and time.time() - marker.stat().st_mtime < RETRY_MISSING_SECONDS:
        return "skipped"
    try:
        resp = httpx.get(_url_for(symbol), timeout=TIMEOUT_SECONDS, follow_redirects=True, headers={"User-Agent": "Strategeia personal dashboard"})
    except httpx.HTTPError:
        return "error"
    if resp.status_code in (404, 410):
        folder.mkdir(parents=True, exist_ok=True)
        marker.touch()
        return "missing"
    if resp.status_code != 200 or len(resp.content) > MAX_BYTES:
        return "error"
    ext = _extension_if_real_image(resp.content)
    if ext is None:
        return "error"
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{symbol}.{ext}.tmp"
    tmp.write_bytes(resp.content)
    tmp.replace(folder / f"{symbol}.{ext}")
    marker.unlink(missing_ok=True)
    return "stored"


def sync_logos(symbols: list[str]) -> dict[str, int]:
    """Fetch every logo not yet on disk. Stops early after a run of failures (a rate limit): the next sync continues."""
    counts = {"stored": 0, "have": 0, "missing": 0, "skipped": 0, "error": 0}
    failures = 0
    for raw in dict.fromkeys(symbols):
        result = fetch_logo(raw)
        counts[result] += 1
        failures = failures + 1 if result == "error" else 0
        if failures >= STOP_AFTER_FAILURES:
            logger.info("Logo sync stopped after %d failures in a row; the next sync continues", failures)
            break
        if result in ("stored", "missing"):
            time.sleep(PAUSE_BETWEEN_DOWNLOADS)
    return counts


def universe_symbols() -> list[str]:
    """The effective universe plus the saved watchlist (they differ while STRATEGEIA_DEV_TICKERS overrides it)."""
    from app.data_providers import universe, universe_store

    symbols = [e.symbol for e in universe.load_universe()]
    saved = universe_store.read_store().watchlist
    if saved:
        symbols += [s.symbol for s in saved.symbols]
    return symbols


def run_sync() -> dict[str, int] | None:
    """One sync of the whole universe; None when another is already running."""
    if not _sync_lock.acquire(blocking=False):
        return None
    try:
        counts = sync_logos(universe_symbols())
        if counts["stored"] or counts["error"]:
            logger.info("Logo sync: %s", counts)
        return counts
    except Exception:
        logger.exception("Logo sync failed")
        return None
    finally:
        _sync_lock.release()


def kick_sync() -> None:
    """Start a sync now in the background (a ticker just joined the universe)."""
    threading.Thread(target=run_sync, name="logo-sync-kick", daemon=True).start()
