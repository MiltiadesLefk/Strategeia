"""One small way to download an RSS/Atom feed for the Fed and posts feeds.

Deliberately plain: one GET with a timeout that follows redirects (the posts
archive redirects its bare domain to the www one). It does not retry. These
feeds are polled every few minutes by a watcher, and a failed poll is already
retried by the watcher runner with a growing pause, so retrying here as well
would only double the load on a source that is probably struggling.

Any failure (a network error, a timeout, a status other than 200 such as the
403 a blocked address gets) becomes a `DataProviderError`. The caller then
records the error and produces no events. The function never returns a
made-up or partial body.

The HTTP function is injectable so the tests never touch the network.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from app.data_providers.base import DataProviderError

REQUEST_TIMEOUT_SECONDS = 20.0
# Names the program honestly; the Fed's feeds and the posts archive ask for nothing more.
USER_AGENT = "Strategeia/1.0 (personal paper-trading research; feed reader)"
# A feed is a few hundred kilobytes at most. Anything far bigger is not a feed.
MAX_FEED_BYTES = 5_000_000

HttpGet = Callable[[str, dict[str, str], float], tuple[int, bytes]]


def _httpx_get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    return response.status_code, response.content


def fetch_feed(url: str, *, http_get: HttpGet = _httpx_get, timeout: float = REQUEST_TIMEOUT_SECONDS) -> bytes:
    """The body of `url`, or a DataProviderError saying what went wrong."""
    try:
        status, body = http_get(url, {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, */*"}, timeout)
    except (httpx.HTTPError, OSError) as exc:
        raise DataProviderError(f"feed fetch failed for {url}: {type(exc).__name__}: {exc}") from exc
    if status != 200:
        raise DataProviderError(f"feed fetch failed for {url}: HTTP {status}")
    if len(body) > MAX_FEED_BYTES:
        raise DataProviderError(f"feed at {url} is larger than {MAX_FEED_BYTES} bytes; refused")
    return body
