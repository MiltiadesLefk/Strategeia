"""Make the news links on the Ratings page open the article: a Finnhub link (`finnhub.io/api/news?id=...`) is a redirect to the
publisher, so it is followed once and the publisher's own address is stored; a link that answers "gone" (404/410) is dropped
and the headline stays as plain text. A 403/429 or a timeout proves nothing (publishers block scripts), so the link is kept.
Display only: nothing decides anything from it. Fixed host only (Finnhub), a few in parallel, cached for the process."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.knowledge import is_simulated

logger = logging.getLogger(__name__)

REDIRECT_PREFIX = "https://finnhub.io/api/news"
MAX_CHECKS_PER_CALL = 12
TIMEOUT_SECONDS = 6.0
_cache: dict[str, str] = {}


def _check(url: str) -> str:
    """The address to show for `url`: the publisher's, the original (unknown), or "" (gone)."""
    if url in _cache:
        return _cache[url]
    result = url
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=TIMEOUT_SECONDS,
                          headers={"User-Agent": "Mozilla/5.0 (compatible; Strategeia/1.0)"}) as r:
            if r.status_code in (404, 410):
                result = ""
            elif r.status_code == 200 and not str(r.url).startswith(REDIRECT_PREFIX):
                result = str(r.url)
    except httpx.HTTPError:
        logger.info("News link check failed for %s", url[:80], exc_info=True)
    _cache[url] = result
    return result


def resolve_item_links(items: list[dict]) -> list[dict]:
    """The same items with their Finnhub links resolved (see the module docstring). Never raises, never in a simulated moment."""
    todo = [it["url"] for it in items if str(it.get("url", "")).startswith(REDIRECT_PREFIX)]
    if not todo or is_simulated():
        return items
    todo = list(dict.fromkeys(todo))[:MAX_CHECKS_PER_CALL]
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            resolved = dict(zip(todo, pool.map(_check, todo)))
    except Exception:  # noqa: BLE001 - a link check must never break a rating
        logger.info("News link check failed", exc_info=True)
        return items
    return [{**it, "url": resolved.get(it.get("url", ""), it.get("url", ""))} for it in items]
