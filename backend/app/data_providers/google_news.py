"""Company headlines from Google News' public search RSS.

Yahoo's per-ticker feed is a "related stocks" basket: for a mid-cap it returns peers, sector pieces and
market roundups, and it can miss the company's own big story. Searching by the company's name and exchange
ticker returns stories that are actually about it, from many publishers.

Unofficial and unkeyed: there is no guarantee it keeps working, so every failure is a `DataProviderError`
and the caller falls back to the other providers. Headlines only, never article text. Every title is
untrusted external text and goes through the same handling as any other headline.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import httpx

from app.data_providers.base import DataProviderError, NewsItem
from app.data_providers.cache import cached
from app.timeutil import utc_iso_from_timestamp

logger = logging.getLogger(__name__)

BASE_URL = "https://news.google.com/rss/search"
NEWS_TTL = 30 * 60
MAX_RESPONSE_BYTES = 1_000_000
REQUEST_TIMEOUT_SECONDS = 10
USER_AGENT = "Mozilla/5.0 (compatible; Strategeia personal dashboard)"


def parse_feed(xml_bytes: bytes) -> list[NewsItem]:
    """Items of an RSS feed, newest first. Raises DataProviderError on anything that is not a plain feed."""
    if len(xml_bytes) > MAX_RESPONSE_BYTES:
        raise DataProviderError("google news feed is unexpectedly large")
    head = xml_bytes[:4096].lower()
    # A news feed has no reason to declare entities; refusing them closes the entity-expansion class of attack.
    if b"<!doctype" in head or b"<!entity" in xml_bytes.lower():
        raise DataProviderError("google news feed declares a DOCTYPE or entities")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise DataProviderError(f"google news feed is not valid XML: {exc}") from exc

    items: list[tuple[float, NewsItem]] = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        publisher = (node.findtext("source") or "").strip() or "Google News"
        suffix = f" - {publisher}"
        if title.endswith(suffix):
            title = title[: -len(suffix)].rstrip()
        if not title:
            continue
        stamp = 0.0
        published_at = ""
        raw_date = (node.findtext("pubDate") or "").strip()
        if raw_date:
            try:
                stamp = parsedate_to_datetime(raw_date).timestamp()
                published_at = utc_iso_from_timestamp(stamp)
            except (TypeError, ValueError):
                pass
        items.append((stamp, NewsItem(headline=title, source=publisher, url=link, published_at=published_at)))
    items.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in items]


class GoogleNewsProvider:
    name = "google_news"

    @cached(NEWS_TTL)
    def search(self, query: str, limit: int = 5, days: int = 7) -> list[NewsItem]:
        """The newest `limit` headlines matching `query` from the last `days` days."""
        url = f"{BASE_URL}?q={quote_plus(f'{query} when:{days}d')}&hl=en-US&gl=US&ceid=US:en"
        try:
            resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DataProviderError(f"google news search failed: {exc}") from exc
        return parse_feed(resp.content)[:limit]


__all__ = ["GoogleNewsProvider", "parse_feed"]
