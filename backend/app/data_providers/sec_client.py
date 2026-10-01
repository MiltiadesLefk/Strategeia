"""One polite, shared HTTP client for SEC EDGAR.

EDGAR's fair-access rule caps a requester at 10 requests per second and answers
429 (or blocks the address) when it is exceeded. The insider-trade code makes
many small requests (one list of filings, then one document per filing), and
more than one thread can be doing so at once (the scheduler, a backfill script,
a request handler), so the limit is enforced in ONE place: a process-wide rate
limiter that every request waits on. It stays at half the allowed rate, because
a bursty neighbour on the same address (another tool, a second process) is
invisible to us.

What this module gives its callers:

  * the app's configured User-Agent (`sec_edgar_provider._headers()`), so the
    requester is always identified the way EDGAR requires;
  * a rate limiter shared by every thread (`RateLimiter`, default 5 per second);
  * retry with exponential backoff on 429, 5xx and network errors, honouring a
    `Retry-After` header, then a clean `DataProviderError` once attempts run out
    (never a half-read body, never a made-up result);
  * a timeout on every request;
  * an on-disk cache for archive documents. A filing under /Archives/ never
    changes once published (an amendment is a NEW filing with its own accession
    number), so a document fetched once is kept and a re-run of a backfill costs
    no requests. Lists that DO change (the submissions index, the ticker map) are
    never written to disk here.

The clock, the sleep function and the HTTP function are all injectable so the
tests exercise the limiter, the retries and the cache without any network or
real waiting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import get_infra_settings
from app.data_providers import health
from app.data_providers.base import DataProviderError
from app.data_providers.sec_edgar_provider import _headers

logger = logging.getLogger(__name__)

# SEC's published ceiling is 10 requests per second. Half of that: a second
# process on the same address would otherwise push the pair over.
MAX_REQUESTS_PER_SECOND = 5.0
REQUEST_TIMEOUT_SECONDS = 20.0
# Total tries per URL (the first plus retries).
MAX_ATTEMPTS = 4
# Waits between attempts: 1 s, 2 s, 4 s ... capped, unless Retry-After says more.
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
# A Retry-After longer than this is not worth blocking a worker for; fail instead
# so the caller can move on and retry later.
RETRY_AFTER_MAX_SECONDS = 60.0
# Status codes that are worth another try. Everything else (403, 404, ...) is
# permanent for this request and fails at once: 403 on EDGAR means the
# User-Agent was rejected, and hammering it only gets the address blocked.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Archive documents are small (a Form 4 is 3-15 KB), so 300 MB holds well over
# ten thousand of them: about a decade of one large company's filings.
DOC_CACHE_MAX_BYTES = 300 * 1024 * 1024
DOC_CACHE_DIRNAME = "sec_archive"
# The name this client's calls are listed under in the Data sources health.
SEC_HEALTH_NAME = "sec_filings"

# `_http_get(url, headers, timeout) -> (status, headers, body)`
HttpGet = Callable[[str, dict[str, str], float], tuple[int, dict[str, str], bytes]]


def _httpx_get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, dict[str, str], bytes]:
    response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    return response.status_code, dict(response.headers), response.content


class RateLimiter:
    """At most `rate` request STARTS per second, across every thread.

    Spacing rather than a token bucket: starts are at least 1/rate apart, so
    there is no burst allowance to blow through the cap. The next free slot is
    reserved under the lock and the actual sleeping happens outside it, so
    waiting threads queue up in order without blocking each other's bookkeeping.
    """

    def __init__(
        self,
        rate: float = MAX_REQUESTS_PER_SECOND,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._interval = 1.0 / rate
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def acquire(self) -> float:
        """Block until a request may start; returns the seconds waited."""
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._interval
            wait = slot - now
        if wait > 0:
            self._sleep(wait)
        return max(wait, 0.0)


@dataclass(frozen=True)
class DocCacheStats:
    files: int
    bytes: int


class DocCache:
    """Immutable archive documents on disk, one file per URL, size-capped.

    Best effort, like the app's other disk caches: an unwritable directory or a
    full disk never fails a fetch, it just means the document is fetched again
    next time. Writes go to a temporary name and are renamed into place so a
    crash never leaves a half-written document that would later be served as if
    complete. When the total passes `max_bytes` the least recently used files
    are deleted first (a hit refreshes the file's modification time).
    """

    def __init__(self, directory: Path, max_bytes: int = DOC_CACHE_MAX_BYTES) -> None:
        self.directory = Path(directory)
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def _path(self, url: str) -> Path:
        return self.directory / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".doc")

    def get(self, url: str) -> bytes | None:
        path = self._path(url)
        try:
            data = path.read_bytes()
            os.utime(path, None)  # recently used, so eviction spares it
            return data
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("sec doc cache read failed for %s: %s", path.name, exc)
            return None

    def put(self, url: str, body: bytes) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self._path(url)
            tmp = path.with_suffix(f".tmp{os.getpid()}-{threading.get_ident()}")
            tmp.write_bytes(body)
            os.replace(tmp, path)
            self._evict()
        except OSError as exc:
            logger.warning("sec doc cache write failed: %s", exc)

    def stats(self) -> DocCacheStats:
        files = list(self._files())
        return DocCacheStats(files=len(files), bytes=sum(size for _, size, _ in files))

    def _files(self):
        try:
            for entry in os.scandir(self.directory):
                if entry.name.endswith(".doc"):
                    info = entry.stat()
                    yield Path(entry.path), info.st_size, info.st_mtime
        except FileNotFoundError:
            return

    def _evict(self) -> None:
        with self._lock:
            files = list(self._files())
            total = sum(size for _, size, _ in files)
            if total <= self.max_bytes:
                return
            for path, size, _ in sorted(files, key=lambda f: f[2]):
                try:
                    path.unlink()
                except OSError:
                    continue
                total -= size
                if total <= self.max_bytes:
                    return


class SecClient:
    """Rate-limited, retrying, caching access to sec.gov / data.sec.gov."""

    def __init__(
        self,
        *,
        limiter: RateLimiter | None = None,
        http_get: HttpGet = _httpx_get,
        doc_cache: DocCache | None = None,
        sleep: Callable[[float], None] = time.sleep,
        headers: Callable[[], dict[str, str]] = _headers,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self.limiter = limiter or RateLimiter(sleep=sleep)
        self._http_get = http_get
        self.doc_cache = doc_cache
        self._sleep = sleep
        self._headers = headers
        self._timeout = timeout
        self._max_attempts = max_attempts
        self.requests_made = 0

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        delay = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass  # an HTTP-date form of Retry-After: the exponential delay will do
        return delay

    def get_bytes(self, url: str) -> bytes:
        """GET `url` once it is allowed, retrying transient failures. Each
        call (retries included) is one entry in the Data sources health."""
        with health.track(SEC_HEALTH_NAME, "get"):
            return self._get_bytes_with_retries(url)

    def _get_bytes_with_retries(self, url: str) -> bytes:
        last_problem = "no attempt made"
        for attempt in range(1, self._max_attempts + 1):
            self.limiter.acquire()
            self.requests_made += 1
            retry_after: str | None = None
            try:
                status, response_headers, body = self._http_get(url, self._headers(), self._timeout)
            except (httpx.HTTPError, OSError) as exc:
                last_problem = f"{type(exc).__name__}: {exc}"
            else:
                if status == 200:
                    return body
                last_problem = f"HTTP {status}"
                if status not in RETRYABLE_STATUS:
                    raise DataProviderError(f"sec fetch failed for {url}: {last_problem}")
                retry_after = {k.lower(): v for k, v in response_headers.items()}.get("retry-after")
                if retry_after:
                    try:
                        if float(retry_after) > RETRY_AFTER_MAX_SECONDS:
                            raise DataProviderError(
                                f"sec fetch failed for {url}: {last_problem}, asked to wait {retry_after}s"
                            )
                    except ValueError:
                        pass
            if attempt < self._max_attempts:
                self._sleep(self._backoff(attempt, retry_after))
        raise DataProviderError(f"sec fetch failed for {url} after {self._max_attempts} attempts: {last_problem}")

    def get_json(self, url: str) -> dict | list:
        """A live JSON document (submissions index, ticker map). Never cached
        on disk: these change as new filings arrive."""
        body = self.get_bytes(url)
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataProviderError(f"sec document at {url} was not valid JSON: {exc}") from exc

    def get_archive_doc(self, url: str) -> bytes:
        """An immutable filing document, served from disk when held."""
        if self.doc_cache is not None:
            cached = self.doc_cache.get(url)
            if cached is not None:
                return cached
        body = self.get_bytes(url)
        if self.doc_cache is not None:
            self.doc_cache.put(url, body)
        return body


_default_client: SecClient | None = None
_default_lock = threading.Lock()


def default_doc_cache_dir() -> Path:
    """Beside the database and settings file (the volume that persists), so a
    redeploy keeps it; disposable, safe to delete."""
    return get_infra_settings().settings_file.parent / DOC_CACHE_DIRNAME


def get_sec_client() -> SecClient:
    """The process-wide client. One instance, so one rate limiter, which is the
    whole point: two callers must never each think they have the full budget."""
    global _default_client
    with _default_lock:
        if _default_client is None:
            _default_client = SecClient(doc_cache=DocCache(default_doc_cache_dir()))
        return _default_client
