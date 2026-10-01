"""The on-disk half of the provider cache: a small SQLite file of encoded results.

cache.py keeps its in-memory layer in front for speed; this file is what lets
that layer start warm after a restart or a redeploy. It is deliberately a
SEPARATE file from the trading database (runtime/cache.db next to
runtime/strategeia.db): it is disposable, it can be deleted at any time without
losing anything, and it must never bloat the backups of the file that matters.

One row per cache key. The same row serves both of the cache's jobs:

  * fresh    - `expires_at` (wall-clock epoch seconds; a monotonic clock can't
               survive a restart) is still in the future, so the value is served
               as a normal hit;
  * last-known-good - the row exists, however old. It is only ever read when a
               live fetch fails (stale-on-error), and never inside
               fresh_data_only(); cache.py enforces both.

Properties this module owns:

  * Best-effort. A disk error, a locked file, a full disk, a corrupt database
    or a value the codec can't carry never raises into a data call: it is
    counted, logged once in a while, and the call carries on as an in-memory
    cache. A database file that isn't a database is deleted and recreated.
  * Cheap writes. `put` encodes the value on the caller's thread (so what is
    stored is a snapshot, not a frame someone may mutate later) and hands the
    bytes to a background writer, which commits them in batches. Readers see
    queued writes immediately. WAL mode + synchronous=NORMAL keep the commits
    themselves cheap.
  * Bounded. Rows older than STALE_MAX_AGE_SECONDS are dropped (a "last known
    good" from weeks ago is no longer worth showing), and when the file grows
    past `max_bytes` the least recently used rows go first.
  * Safe to share between threads. One connection, one lock; the scheduler and
    request threads both use it.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.data_providers.cache_codec import CODEC_VERSION, CodecError, decode_value, encode_value

logger = logging.getLogger(__name__)

# Total encoded bytes kept. Years of daily bars for the whole S&P 500 are not
# stored here (the price-history store owns those), so what lands in this file
# is mostly a year of bars, a quote, a company overview, news, per symbol: tens
# of kilobytes each. 200 MB is room for a long time and a hard ceiling for ever.
DEFAULT_MAX_BYTES = 200 * 1024 * 1024
# When pruning for size, go down to this fraction of the cap so the next write
# doesn't immediately trigger another prune.
PRUNE_TARGET_FRACTION = 0.9
# A last-known-good older than this is dropped: it only exists to keep a page
# rendering through an outage, and an outage + redeploy rarely outlasts a couple
# of weeks. Past that the honest answer is an error, not a month-old number.
STALE_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
# Pruning is a couple of cheap queries, but not worth running on every write.
PRUNE_EVERY_WRITES = 100
# A single result bigger than this fraction of the cap is not persisted: it
# would push everything else out. (Memory still caches it.)
MAX_ENTRY_FRACTION = 0.25
# ...nor is one bigger than this many (compressed) bytes. A year of daily bars is
# ~12 KB and five years ~60 KB; a result past 100 KB is a multi-year download,
# which belongs in the price-history store (kept on purpose, extended
# incrementally) and not in a cache that expires and gets pruned. Without this a
# bulk history preload would fill the cache with a second copy of every symbol.
MAX_ENTRY_BYTES = 100 * 1024
# How long the writer waits to gather more writes into one commit.
WRITE_BATCH_DELAY_SECONDS = 0.5
# SQLite's own wait when another connection holds the file.
BUSY_TIMEOUT_MS = 3000
# Don't log every failure of a persistent fault (a full disk fails every write).
LOG_EVERY_N_ERRORS = 50

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_entry (
    key        TEXT PRIMARY KEY,
    prefix     TEXT NOT NULL,
    value      BLOB NOT NULL,
    expires_at REAL NOT NULL,
    stored_at  REAL NOT NULL,
    last_used  REAL NOT NULL,
    size       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_entry_last_used ON cache_entry(last_used);
"""


@dataclass
class PersistentCacheStats:
    enabled: bool
    path: str
    entries: int
    fresh_entries: int
    payload_bytes: int
    file_bytes: int
    oldest_stored_at: float | None
    newest_stored_at: float | None
    max_bytes: int
    pending_writes: int
    errors: int
    skipped_unencodable: int


class PersistentCacheStore:
    """See the module docstring. Construct one per file; `close()` when done."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        stale_max_age_seconds: float = STALE_MAX_AGE_SECONDS,
        clock: Callable[[], float] = time.time,
        write_behind: bool = True,
        batch_delay_seconds: float = WRITE_BATCH_DELAY_SECONDS,
    ) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.stale_max_age_seconds = stale_max_age_seconds
        self._clock = clock
        self._write_behind = write_behind
        self._batch_delay = batch_delay_seconds

        self._db_lock = threading.RLock()  # the one connection
        self._pending_lock = threading.Lock()
        self._pending: dict[str, tuple] = {}  # key -> row tuple, not yet committed
        self._touched: dict[str, float] = {}  # key -> last_used, not yet committed
        self._wake = threading.Event()
        self._closed = False
        self._writer: threading.Thread | None = None
        self._writes_since_prune = 0
        self._errors = 0
        self._skipped = 0
        self._conn: sqlite3.Connection | None = None
        self.enabled = False
        self._open()

    # ---- connection ------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=BUSY_TIMEOUT_MS / 1000, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, CODEC_VERSION):
                # Written by a build with a different codec: nothing in it is readable.
                conn.execute("DROP TABLE IF EXISTS cache_entry")
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version={CODEC_VERSION}")
            conn.commit()
        except Exception:
            conn.close()  # an open handle would stop the file being replaced (Windows)
            raise
        return conn

    def _open(self) -> None:
        for attempt in (1, 2):
            try:
                self._conn = self._connect()
                self.enabled = True
                self._prune()
                return
            except sqlite3.DatabaseError as exc:
                # "file is not a database" / malformed: the cache is disposable,
                # so start over rather than run without it.
                self._close_conn()
                if attempt == 1:
                    logger.warning("Cache file %s is unreadable (%s); recreating it.", self.path, exc)
                    self._remove_files()
                    continue
                self._note_error("opening the cache file", exc)
            except OSError as exc:
                self._close_conn()
                self._note_error("opening the cache file", exc)
                break
        self.enabled = False

    def _remove_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(f"{self.path}{suffix}").unlink(missing_ok=True)
            except OSError:
                pass

    def _close_conn(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def _note_error(self, what: str, exc: Exception) -> None:
        self._errors += 1
        if self._errors == 1 or self._errors % LOG_EVERY_N_ERRORS == 0:
            logger.warning("Persistent cache problem while %s (%d so far): %s", what, self._errors, exc)

    # ---- reads -------------------------------------------------------------

    def get(self, key: str, *, allow_expired: bool = False) -> tuple[bool, object, float]:
        """-> (found, value, expires_at). `found` is False for a missing row, an
        expired row when `allow_expired` is False, and any row that won't decode
        (which is also deleted so it isn't retried forever)."""
        if not self.enabled:
            return False, None, 0.0
        now = self._clock()
        with self._pending_lock:
            pending = self._pending.get(key)
        if pending is not None:
            _prefix, blob, expires_at, _stored, _size = pending
        else:
            try:
                with self._db_lock:
                    row = self._conn.execute(  # type: ignore[union-attr]
                        "SELECT value, expires_at FROM cache_entry WHERE key = ?", (key,)
                    ).fetchone()
            except sqlite3.Error as exc:
                self._note_error("reading an entry", exc)
                return False, None, 0.0
            if row is None:
                return False, None, 0.0
            blob, expires_at = row
        if expires_at <= now and not allow_expired:
            return False, None, 0.0
        try:
            value = decode_value(blob)
        except CodecError as exc:
            logger.debug("Dropping undecodable cache row: %s", exc)
            self.delete(key)
            return False, None, 0.0
        with self._pending_lock:
            self._touched[key] = now
        self._wake_writer()
        return True, value, expires_at

    # ---- writes ------------------------------------------------------------

    def put(self, key: str, prefix: str, value: object, expires_at: float) -> bool:
        """Queue `value` for persistence. False if it was not stored (disabled,
        unencodable, or too big); never raises."""
        if not self.enabled or self._closed:
            return False
        try:
            blob = encode_value(value)
        except CodecError:
            self._skipped += 1
            return False
        except Exception as exc:  # an encoder bug must not break a data call
            self._note_error("encoding a value", exc)
            return False
        if len(blob) > min(self.max_bytes * MAX_ENTRY_FRACTION, MAX_ENTRY_BYTES):
            self._skipped += 1
            return False
        now = self._clock()
        with self._pending_lock:
            self._pending[key] = (prefix, blob, float(expires_at), now, len(blob))
        if self._write_behind:
            self._ensure_writer()
            self._wake_writer()
        else:
            self.flush()
        return True

    def delete(self, key: str) -> None:
        if not self.enabled:
            return
        with self._pending_lock:
            self._pending.pop(key, None)
            self._touched.pop(key, None)
        try:
            with self._db_lock:
                self._conn.execute("DELETE FROM cache_entry WHERE key = ?", (key,))  # type: ignore[union-attr]
                self._conn.commit()  # type: ignore[union-attr]
        except sqlite3.Error as exc:
            self._note_error("deleting an entry", exc)

    def clear(self) -> None:
        """Drop every row (and every queued write)."""
        with self._pending_lock:
            self._pending.clear()
            self._touched.clear()
        if not self.enabled:
            return
        try:
            with self._db_lock:
                self._conn.execute("DELETE FROM cache_entry")  # type: ignore[union-attr]
                self._conn.commit()  # type: ignore[union-attr]
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # type: ignore[union-attr]
        except sqlite3.Error as exc:
            self._note_error("clearing the cache", exc)

    def flush(self) -> None:
        """Commit everything queued, on the calling thread. Idempotent and cheap
        when nothing is queued; used on shutdown and by tests."""
        if not self.enabled:
            return
        with self._pending_lock:
            rows = self._pending
            touched = self._touched
            self._pending = {}
            self._touched = {}
        if not rows and not touched:
            return
        try:
            with self._db_lock:
                conn = self._conn
                if conn is None:
                    return
                conn.executemany(
                    "INSERT INTO cache_entry (key, prefix, value, expires_at, stored_at, last_used, size) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET prefix=excluded.prefix, value=excluded.value, "
                    "expires_at=excluded.expires_at, stored_at=excluded.stored_at, "
                    "last_used=excluded.last_used, size=excluded.size",
                    [(k, p, b, e, s, s, n) for k, (p, b, e, s, n) in rows.items()],
                )
                if touched:
                    conn.executemany(
                        "UPDATE cache_entry SET last_used = ? WHERE key = ?",
                        [(when, k) for k, when in touched.items() if k not in rows],
                    )
                conn.commit()
                self._writes_since_prune += len(rows)
                if self._writes_since_prune >= PRUNE_EVERY_WRITES:
                    self._prune()
        except sqlite3.Error as exc:
            self._note_error("writing entries", exc)

    # ---- background writer -------------------------------------------------

    def _ensure_writer(self) -> None:
        if self._writer is not None and self._writer.is_alive():
            return
        with self._pending_lock:
            if self._writer is not None and self._writer.is_alive():
                return
            self._writer = threading.Thread(target=self._writer_loop, name="cache-writer", daemon=True)
            self._writer.start()

    def _wake_writer(self) -> None:
        if self._write_behind:
            self._ensure_writer()
            self._wake.set()

    def _writer_loop(self) -> None:
        while not self._closed:
            self._wake.wait()
            if self._closed:
                break
            # Let a burst (an auto-scan pass fills hundreds of keys) land in one commit.
            time.sleep(self._batch_delay)
            self._wake.clear()
            try:
                self.flush()
            except Exception as exc:  # the loop must not die and stop persisting
                self._note_error("in the cache writer", exc)

    # ---- pruning -----------------------------------------------------------

    def _prune(self) -> None:
        """Drop rows past the age limit, then least-recently-used rows until
        the total is under the size cap. Caller holds the db lock (or is _open)."""
        self._writes_since_prune = 0
        conn = self._conn
        if conn is None:
            return
        try:
            conn.execute("DELETE FROM cache_entry WHERE stored_at < ?", (self._clock() - self.stale_max_age_seconds,))
            total = conn.execute("SELECT COALESCE(SUM(size), 0) FROM cache_entry").fetchone()[0]
            if total > self.max_bytes:
                target = self.max_bytes * PRUNE_TARGET_FRACTION
                doomed: list[tuple[str]] = []
                for key, size in conn.execute("SELECT key, size FROM cache_entry ORDER BY last_used ASC"):
                    if total <= target:
                        break
                    doomed.append((key,))
                    total -= size
                conn.executemany("DELETE FROM cache_entry WHERE key = ?", doomed)
            conn.commit()
        except sqlite3.Error as exc:
            self._note_error("pruning", exc)

    def prune(self) -> None:
        """Flush, then prune now (the writer prunes on its own every so often)."""
        self.flush()
        with self._db_lock:
            self._prune()

    # ---- introspection / lifecycle ----------------------------------------

    def stats(self) -> PersistentCacheStats:
        """Read-only: counts what is committed (queued writes are reported as
        `pending_writes`, not forced to disk, so asking never writes)."""
        entries = fresh = payload = 0
        oldest = newest = None
        file_bytes = 0
        if self.enabled:
            try:
                with self._db_lock:
                    entries, payload, oldest, newest = self._conn.execute(  # type: ignore[union-attr]
                        "SELECT COUNT(*), COALESCE(SUM(size), 0), MIN(stored_at), MAX(stored_at) FROM cache_entry"
                    ).fetchone()
                    fresh = self._conn.execute(  # type: ignore[union-attr]
                        "SELECT COUNT(*) FROM cache_entry WHERE expires_at > ?", (self._clock(),)
                    ).fetchone()[0]
            except sqlite3.Error as exc:
                self._note_error("reading statistics", exc)
        for suffix in ("", "-wal"):
            try:
                file_bytes += Path(f"{self.path}{suffix}").stat().st_size
            except OSError:
                pass
        with self._pending_lock:
            pending = len(self._pending)
        return PersistentCacheStats(
            enabled=self.enabled,
            path=str(self.path),
            entries=entries,
            fresh_entries=fresh,
            payload_bytes=payload,
            file_bytes=file_bytes,
            oldest_stored_at=oldest,
            newest_stored_at=newest,
            max_bytes=self.max_bytes,
            pending_writes=pending,
            errors=self._errors,
            skipped_unencodable=self._skipped,
        )

    def close(self) -> None:
        """Flush and release the file. The writer thread is a daemon and stops on its own."""
        if self._closed:
            return
        self.flush()
        self._closed = True
        self._wake.set()
        with self._db_lock:
            self._close_conn()
        self.enabled = False
