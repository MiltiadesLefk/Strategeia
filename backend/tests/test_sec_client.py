"""The shared SEC client: rate limit, retries, timeouts, disk cache. No network:
the HTTP function, the clock and the sleep are all fakes."""

from __future__ import annotations

import threading

import httpx
import pytest

from app.data_providers.base import DataProviderError
from app.data_providers.sec_client import DocCache, RateLimiter, SecClient

URL = "https://data.sec.gov/submissions/CIK0000320193.json"


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(responses, clock=None, **kwargs):
    """`responses`: list of (status, headers, body) or an Exception, served in order."""
    clock = clock or FakeClock()
    calls: list[tuple[str, dict, float]] = []
    queue = list(responses)

    def http_get(url, headers, timeout):
        calls.append((url, headers, timeout))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    limiter = RateLimiter(rate=5, clock=clock.monotonic, sleep=clock.sleep)
    client = SecClient(limiter=limiter, http_get=http_get, sleep=clock.sleep, **kwargs)
    return client, calls, clock


def test_limiter_spaces_request_starts_at_the_configured_rate():
    clock = FakeClock()
    limiter = RateLimiter(rate=5, clock=clock.monotonic, sleep=clock.sleep)
    waits = [limiter.acquire() for _ in range(5)]
    assert waits[0] == 0
    assert waits[1:] == pytest.approx([0.2, 0.2, 0.2, 0.2])
    # Five starts took four gaps: 0.8 s of the fake clock, so 5 per second at most.
    assert clock.now - 100.0 == pytest.approx(0.8)


def test_limiter_does_not_wait_after_an_idle_gap():
    clock = FakeClock()
    limiter = RateLimiter(rate=5, clock=clock.monotonic, sleep=clock.sleep)
    limiter.acquire()
    clock.now += 10
    assert limiter.acquire() == 0


def test_limiter_reserves_distinct_slots_across_threads():
    # A frozen clock and a recording sleep: 12 threads must be handed 12
    # different slots, i.e. the waits are exactly 0, 0.2, 0.4 ... in some order.
    waits: list[float] = []
    lock = threading.Lock()
    limiter = RateLimiter(rate=5, clock=lambda: 50.0, sleep=lambda s: None)

    def work():
        waited = limiter.acquire()
        with lock:
            waits.append(waited)

    threads = [threading.Thread(target=work) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(waits) == pytest.approx([i * 0.2 for i in range(12)])


def test_sends_the_configured_user_agent_and_a_timeout():
    client, calls, _ = make_client([(200, {}, b"{}")])
    client.get_bytes(URL)
    _, headers, timeout = calls[0]
    assert "User-Agent" in headers and headers["User-Agent"]
    assert timeout > 0


def test_retries_429_with_exponential_backoff_then_succeeds():
    client, calls, clock = make_client([(429, {}, b""), (503, {}, b""), (200, {}, b"ok")])
    assert client.get_bytes(URL) == b"ok"
    assert len(calls) == 3
    backoffs = [s for s in clock.sleeps if s >= 1.0]
    assert backoffs == [1.0, 2.0]


def test_retry_after_header_is_honoured():
    client, _, clock = make_client([(429, {"Retry-After": "7"}, b""), (200, {}, b"ok")])
    client.get_bytes(URL)
    assert 7.0 in clock.sleeps


def test_retry_after_longer_than_a_minute_fails_fast():
    client, calls, _ = make_client([(429, {"Retry-After": "600"}, b"")])
    with pytest.raises(DataProviderError, match="asked to wait"):
        client.get_bytes(URL)
    assert len(calls) == 1


def test_a_403_is_not_retried():
    client, calls, _ = make_client([(403, {}, b"blocked")])
    with pytest.raises(DataProviderError, match="HTTP 403"):
        client.get_bytes(URL)
    assert len(calls) == 1


def test_network_errors_retry_then_raise_a_clean_error():
    client, calls, _ = make_client([httpx.ConnectTimeout("slow")] * 4)
    with pytest.raises(DataProviderError, match="after 4 attempts"):
        client.get_bytes(URL)
    assert len(calls) == 4


def test_get_json_rejects_a_non_json_body():
    client, _, _ = make_client([(200, {}, b"<html>")])
    with pytest.raises(DataProviderError, match="not valid JSON"):
        client.get_json(URL)


def test_archive_documents_are_cached_on_disk(tmp_path):
    cache = DocCache(tmp_path / "docs")
    client, calls, _ = make_client([(200, {}, b"<xml/>")], doc_cache=cache)
    doc_url = "https://www.sec.gov/Archives/edgar/data/320193/000032019322000076/form4.xml"
    assert client.get_archive_doc(doc_url) == b"<xml/>"
    assert client.get_archive_doc(doc_url) == b"<xml/>"  # a second call would pop from an empty queue
    assert len(calls) == 1
    # A fresh client over the same directory (a restart) still hits the cache.
    again, calls2, _ = make_client([], doc_cache=DocCache(tmp_path / "docs"))
    assert again.get_archive_doc(doc_url) == b"<xml/>"
    assert calls2 == []


def test_live_json_is_never_written_to_the_disk_cache(tmp_path):
    cache = DocCache(tmp_path / "docs")
    client, _, _ = make_client([(200, {}, b"{}")], doc_cache=cache)
    client.get_json(URL)
    assert cache.stats().files == 0


def test_doc_cache_evicts_least_recently_used_over_the_size_cap(tmp_path):
    import os

    cache = DocCache(tmp_path, max_bytes=250)
    cache.put("a", b"x" * 100)
    cache.put("b", b"y" * 100)
    os.utime(cache._path("a"), (1_000, 1_000))  # a is the oldest
    os.utime(cache._path("b"), (2_000, 2_000))
    cache.put("c", b"z" * 100)  # 300 bytes > 250: the oldest goes
    assert cache.get("a") is None
    assert cache.get("b") == b"y" * 100
    assert cache.get("c") == b"z" * 100
    assert cache.stats().bytes <= 250


def test_unwritable_cache_directory_does_not_break_a_fetch(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    client, _, _ = make_client([(200, {}, b"body")], doc_cache=DocCache(blocker / "sub"))
    assert client.get_archive_doc("https://www.sec.gov/Archives/x") == b"body"
