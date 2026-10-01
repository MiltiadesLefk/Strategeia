"""How each data source is behaving: calls, success rate, latency, last error.

The composite provider (and the few non-chain clients: SEC, FINRA, ECB, FRED)
report every call here, once, centrally. Nothing is invented: a source that has
never been called has no numbers, and the Settings card says so.

What is kept, per (source, method):

  * lifetime counters since the process started: calls, successes, failures,
    cache hits and misses, "old data served because the live call failed";
  * a rolling window of the last WINDOW_SIZE calls (when, ok or not, how long it
    took, whether it was answered from old cached data). Percentiles and the
    status label come from the window, so one bad hour last week does not
    colour today;
  * the current run of consecutive failures, the last success time and the
    last error text.

Rules this module keeps:

  * **It never raises.** Recording is a side effect on the hot path of every
    data call; a bug here must not turn into a failed scan.
  * **It is thread-safe** (one lock, held for a few dictionary operations).
  * **It is OFF while a moment is being simulated** (a backtest or replay
    drives the same providers with historical data; counting those calls would
    make the live health look like whatever the backtest did).
  * **Errors are scrubbed**: truncated, and any URL query string or key-like
    parameter is blanked, so a provider's API key never reaches the screen.

Cache hits are not latency samples. The cache layer tells this module what it
did (`note_cache`) through a context variable, so a 0.1 ms memory hit does not
drag the median down and make a slow provider look fast.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# The rolling window each percentile and status is computed from.
WINDOW_SIZE = 100
# Error text is shortened to this many characters before it is stored.
MAX_ERROR_CHARS = 200

# Status thresholds, over the window and the current failure run. One failure
# is normal on a fallback chain (an unknown symbol makes every provider fail
# once), so a single miss never changes the label.
FAILING_CONSECUTIVE = 3  # this many failures in a row
FAILING_SUCCESS_RATE = 0.5  # or fewer than half of the window succeeded
DEGRADED_CONSECUTIVE = 2
DEGRADED_SUCCESS_RATE = 0.9
# Old cached data served inside the window means the live source failed lately.
DEGRADED_ON_STALE = True

STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_FAILING = "failing"
STATUS_UNUSED = "unused"

_Outcome = str  # "hit" | "miss" | "stale"
# What the cache layer did for the call in progress on this thread.
_cache_outcome: ContextVar[_Outcome | None] = ContextVar("strategeia_cache_outcome", default=None)

_URL_QUERY = re.compile(r"(https?://[^\s?#]+)\?[^\s]*")
_KEY_PARAM = re.compile(r"(?i)\b(api[_-]?key|apikey|token|secret|password|auth|key)=[^&\s'\"]+")


def scrub_error(error: object) -> str:
    """Error text safe to keep and show: query strings blanked, key-like
    parameters hidden, length capped."""
    text = str(error) if error is not None else ""
    text = _URL_QUERY.sub(r"\1?...", text)
    text = _KEY_PARAM.sub(lambda m: f"{m.group(1)}=***", text)
    text = " ".join(text.split())
    if len(text) > MAX_ERROR_CHARS:
        text = text[: MAX_ERROR_CHARS - 1] + "…"
    return text


def percentile(values: list[float], q: float) -> float | None:
    """The q-th percentile (0-100) by linear interpolation between ranks, the
    same convention as numpy's default. None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (q / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


@dataclass
class _Sample:
    at: float  # epoch seconds
    ok: bool
    latency_ms: float | None  # None for a cache hit: no upstream call happened
    stale: bool = False


@dataclass
class _Entry:
    calls: int = 0
    successes: int = 0
    failures: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    stale_served: int = 0
    consecutive_failures: int = 0
    last_used_at: float | None = None
    last_success_at: float | None = None
    last_error: str | None = None
    last_error_at: float | None = None
    samples: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))


@dataclass(frozen=True)
class MethodHealth:
    method: str
    calls: int
    successes: int
    failures: int
    consecutive_failures: int
    last_success_at: float | None
    last_error: str | None


@dataclass(frozen=True)
class SourceHealth:
    """One source's numbers, all methods combined."""

    name: str
    calls: int
    successes: int
    failures: int
    window_calls: int
    window_success_rate: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    latency_samples: int
    consecutive_failures: int
    last_used_at: float | None
    last_success_at: float | None
    last_error: str | None
    last_error_at: float | None
    cache_hits: int
    cache_lookups: int
    cache_hit_ratio: float | None
    stale_served: int
    stale_in_window: int
    status: str
    methods: tuple[MethodHealth, ...]


_lock = threading.Lock()
_entries: dict[tuple[str, str], _Entry] = {}
_clock: Callable[[], float] = time.time


def _recording_allowed() -> bool:
    try:
        from app.knowledge.point_in_time import is_simulated

        return not is_simulated()
    except Exception:  # never let bookkeeping break a data call
        return True


def reset() -> None:
    """Forget everything (tests, and the start of a fresh process)."""
    with _lock:
        _entries.clear()


def _split_prefix(prefix: str) -> tuple[str, str]:
    provider, _, method = prefix.partition(".")
    return provider, method or "call"


def note_cache(prefix: str, outcome: _Outcome) -> None:
    """The cache layer reports what it did for one lookup: "hit", "miss" (went
    to the source) or "stale" (the source failed, old data was served).
    `prefix` is the cache key prefix, "<provider>.<method>"."""
    try:
        _cache_outcome.set(outcome)
        if not _recording_allowed():
            return
        key = _split_prefix(prefix)
        with _lock:
            entry = _entries.setdefault(key, _Entry())
            if outcome == "hit":
                entry.cache_hits += 1
            else:
                entry.cache_misses += 1
                if outcome == "stale":
                    entry.stale_served += 1
    except Exception:
        logger.debug("health.note_cache failed", exc_info=True)


def begin_call() -> None:
    """Clear the cache outcome before a call, so one call's outcome is never
    read as the next call's."""
    try:
        _cache_outcome.set(None)
    except Exception:
        pass


def record_call(
    source: str,
    method: str,
    ok: bool,
    latency_ms: float,
    error: object = None,
    *,
    read_cache_outcome: bool = False,
) -> None:
    """Record one finished call. Safe to call from anywhere; never raises.

    `read_cache_outcome` is for callers that cleared the outcome with
    `begin_call()` just before the call (the composite provider and `track`):
    only then is what the cache layer reported really about THIS call."""
    try:
        outcome = _cache_outcome.get() if read_cache_outcome else None
        _cache_outcome.set(None)
        if not _recording_allowed():
            return
        now = _clock()
        sample = _Sample(
            at=now,
            ok=ok,
            latency_ms=None if outcome == "hit" else max(float(latency_ms), 0.0),
            stale=outcome == "stale",
        )
        with _lock:
            entry = _entries.setdefault((source, method), _Entry())
            entry.calls += 1
            entry.last_used_at = now
            entry.samples.append(sample)
            if ok:
                entry.successes += 1
                entry.consecutive_failures = 0
                entry.last_success_at = now
            else:
                entry.failures += 1
                entry.consecutive_failures += 1
                entry.last_error = scrub_error(error)
                entry.last_error_at = now
    except Exception:
        logger.debug("health.record_call failed", exc_info=True)


@contextmanager
def track(source: str, method: str) -> Iterator[None]:
    """Time the block and record it as one call to `source`. A raised exception
    is recorded as a failure and re-raised unchanged."""
    begin_call()
    started = time.perf_counter()
    try:
        yield
    except BaseException as exc:
        record_call(source, method, False, (time.perf_counter() - started) * 1000.0, exc, read_cache_outcome=True)
        raise
    else:
        record_call(source, method, True, (time.perf_counter() - started) * 1000.0, read_cache_outcome=True)


def classify(
    calls: int,
    window_success_rate: float | None,
    consecutive_failures: int,
    stale_in_window: int,
) -> str:
    if calls == 0:
        return STATUS_UNUSED
    if consecutive_failures >= FAILING_CONSECUTIVE or (
        window_success_rate is not None and window_success_rate < FAILING_SUCCESS_RATE
    ):
        return STATUS_FAILING
    if (
        consecutive_failures >= DEGRADED_CONSECUTIVE
        or (window_success_rate is not None and window_success_rate < DEGRADED_SUCCESS_RATE)
        or (DEGRADED_ON_STALE and stale_in_window > 0)
    ):
        return STATUS_DEGRADED
    return STATUS_HEALTHY


def _max_or_none(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _combine(name: str, parts: list[tuple[str, _Entry]]) -> SourceHealth:
    samples = [s for _, e in parts for s in e.samples]
    # Keep the newest WINDOW_SIZE across methods, so the window means the same
    # thing for a source with one method as for one with seven.
    samples.sort(key=lambda s: s.at)
    samples = samples[-WINDOW_SIZE:]
    window_ok = sum(1 for s in samples if s.ok)
    latencies = [s.latency_ms for s in samples if s.latency_ms is not None]
    calls = sum(e.calls for _, e in parts)
    hits = sum(e.cache_hits for _, e in parts)
    lookups = hits + sum(e.cache_misses for _, e in parts)
    consecutive = max((e.consecutive_failures for _, e in parts), default=0)
    stale_in_window = sum(1 for s in samples if s.stale)
    success_rate = (window_ok / len(samples)) if samples else None
    last_error_entry = max(
        (e for _, e in parts if e.last_error_at is not None), key=lambda e: e.last_error_at or 0.0, default=None
    )
    return SourceHealth(
        name=name,
        calls=calls,
        successes=sum(e.successes for _, e in parts),
        failures=sum(e.failures for _, e in parts),
        window_calls=len(samples),
        window_success_rate=success_rate,
        latency_p50_ms=percentile(latencies, 50),
        latency_p95_ms=percentile(latencies, 95),
        latency_samples=len(latencies),
        consecutive_failures=consecutive,
        last_used_at=_max_or_none([e.last_used_at for _, e in parts]),
        last_success_at=_max_or_none([e.last_success_at for _, e in parts]),
        last_error=last_error_entry.last_error if last_error_entry else None,
        last_error_at=last_error_entry.last_error_at if last_error_entry else None,
        cache_hits=hits,
        cache_lookups=lookups,
        cache_hit_ratio=(hits / lookups) if lookups else None,
        stale_served=sum(e.stale_served for _, e in parts),
        stale_in_window=stale_in_window,
        status=classify(calls, success_rate, consecutive, stale_in_window),
        methods=tuple(
            MethodHealth(
                method=method,
                calls=e.calls,
                successes=e.successes,
                failures=e.failures,
                consecutive_failures=e.consecutive_failures,
                last_success_at=e.last_success_at,
                last_error=e.last_error,
            )
            for method, e in sorted(parts, key=lambda p: p[0])
        ),
    )


def snapshot() -> dict[str, SourceHealth]:
    """Every source that has been seen, by name. A read-only copy."""
    with _lock:
        grouped: dict[str, list[tuple[str, _Entry]]] = {}
        for (source, method), entry in _entries.items():
            # Copy so the maths below runs outside the lock on stable data.
            copy = _Entry(**{k: getattr(entry, k) for k in entry.__dataclass_fields__ if k != "samples"})
            copy.samples = deque(entry.samples, maxlen=WINDOW_SIZE)
            grouped.setdefault(source, []).append((method, copy))
    return {name: _combine(name, parts) for name, parts in grouped.items()}


def empty_health(name: str) -> SourceHealth:
    """What a source that was never called looks like."""
    return _combine(name, [])
