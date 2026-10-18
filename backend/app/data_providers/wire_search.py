"""Market-wide press-release headlines through Google News RSS, restricted to the four big wires.

Why: GlobeNewswire and Business Wire publish no scriptable all-news feed (GlobeNewswire answers scripts 403, Business Wire
only hands out opaque per-category ids), but Google News RSS indexes both, plus PR Newswire and ACCESS Newswire, across the
whole market. Each query asks for releases that contain an exchange tag ("NASDAQ:" / "NYSE:", so the issuer is listed) and a
big-news keyword. Google's title carries no ticker, so the company is matched to a ticker by its NAME against the SEC's own
company list: the headline must START with the company's full name (suffixes like Inc/Corp ignored), the longest match
wins, and a name shared by two different companies is dropped. A wrong match is still possible for a short one-word name;
the caller (`big_news_service`) re-checks the headline, requires a live quote and runs the quality gate.

Politeness: same paced, cached, size-limited fetch as `pr_newswire.fetch_feed_text`; one query per family (seven) per run.
"""

from __future__ import annotations

import logging
import math
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

from app.data_providers.base import DataProviderError
from app.data_providers.pr_newswire import fetch_feed_text
from app.knowledge import to_naive_utc

logger = logging.getLogger(__name__)

WIRE_SITES = "(site:globenewswire.com OR site:businesswire.com OR site:prnewswire.com OR site:accessnewswire.com)"
# One query per big-news family; the rules in analysis/big_news.py still decide what counts.
KEYWORD_QUERIES: tuple[str, ...] = (
    '"FDA approval" OR "FDA approves" OR "FDA clearance"',
    '"awarded" OR "wins contract" OR "contract award"',
    '"to acquire" OR "definitive agreement" OR "acquisition offer"',
    '"raises guidance" OR "raises outlook" OR "record revenue" OR "beats"',
    '"share repurchase" OR "buyback"',
    '"loan" OR "financing" OR "grant" OR "equity stake" OR "billion investment"',
    '"partnership" OR "strategic agreement" OR "collaboration agreement"',
)
SEARCH_URL = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
TICKER_MAP_TTL_SECONDS = 24 * 60 * 60
MIN_NAME_LENGTH = 5
MAX_NAME_TOKENS = 6
_SUFFIXES = frozenset(
    "inc incorporated corp corporation co company ltd limited plc holdings holding group llc lp sa nv ag the de".split()
)
_name_map: tuple[float, dict[str, str]] | None = None


@dataclass(frozen=True)
class WireHeadline:
    title: str
    published_at: datetime
    source: str


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower().replace("&", " and ")).split()


def _name_key(name: str) -> str:
    return " ".join(t for t in _tokens(name) if t not in _SUFFIXES)


def build_name_map(entries: list[dict]) -> dict[str, str]:
    """normalised company name -> ticker, from SEC company_tickers.json rows. A name used by two different
    companies (different CIK) is dropped; one company with several tickers keeps the shortest."""
    best: dict[str, tuple[int, str]] = {}
    ambiguous: set[str] = set()
    for e in entries:
        ticker, title = str(e.get("ticker") or "").upper(), str(e.get("title") or "")
        key = _name_key(title)
        if not ticker or len(key) < MIN_NAME_LENGTH:
            continue
        cik = int(e.get("cik_str") or 0)
        if key in best and best[key][0] != cik:
            ambiguous.add(key)
        if key not in best or (best[key][0] == cik and len(ticker) < len(best[key][1])):
            best[key] = (cik, ticker)
    return {k: v[1] for k, v in best.items() if k not in ambiguous}


def _load_name_map() -> dict[str, str]:
    global _name_map
    if _name_map is None or time.monotonic() - _name_map[0] > TICKER_MAP_TTL_SECONDS:
        from app.data_providers.sec_client import get_sec_client

        payload = get_sec_client().get_json("https://www.sec.gov/files/company_tickers.json")
        if not isinstance(payload, dict):
            raise DataProviderError("sec ticker map had an unexpected shape")
        _name_map = (time.monotonic(), build_name_map(list(payload.values())))
    return _name_map[1]


def ticker_for_headline(title: str, name_map: dict[str, str]) -> str | None:
    """The ticker whose full company name opens the headline (longest name wins), else None."""
    words = _tokens(title)
    for k in range(min(MAX_NAME_TOKENS, len(words)), 0, -1):
        ticker = name_map.get(_name_key(" ".join(words[:k])))
        if ticker and _name_key(" ".join(words[:k])) == " ".join(words[:k]):
            return ticker
    return None


def parse_results(xml_text: str) -> list[WireHeadline]:
    if not xml_text.strip() or "<!doctype" in xml_text[:2000].lower() or "<!entity" in xml_text.lower():
        raise ValueError("empty or unsafe feed")
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError as exc:
        raise ValueError(f"not valid XML: {exc}") from exc
    out: list[WireHeadline] = []
    for item in root.iter("item"):
        title, source = (item.findtext("title") or "").strip(), (item.findtext("source") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].strip()
        try:
            parsed = parsedate_to_datetime((item.findtext("pubDate") or "").strip())
        except (TypeError, ValueError, IndexError):
            continue
        if title and parsed is not None and parsed.tzinfo is not None:
            out.append(WireHeadline(title, to_naive_utc(parsed), source))
    return out


def search_wire_headlines(lookback_hours: float) -> list[WireHeadline]:
    """Recent exchange-tagged big-wire releases across the market; a failed query is skipped."""
    days = max(1, math.ceil(lookback_hours / 24))
    found: dict[str, WireHeadline] = {}
    for kw in KEYWORD_QUERIES:
        query = f'("NASDAQ:" OR "NYSE:") {WIRE_SITES} ({kw}) when:{days}d'
        try:
            for h in parse_results(fetch_feed_text(SEARCH_URL.format(q=quote_plus(query)))):
                found.setdefault(h.title, h)
        except (DataProviderError, ValueError):
            logger.info("Big news: wire search %r skipped", kw)
    return list(found.values())


def wire_ticker_headlines(lookback_hours: float, now: datetime) -> dict[str, str]:
    """{ticker: headline} for search results inside the window whose company name maps to one ticker."""
    name_map = _load_name_map()
    out: dict[str, str] = {}
    for h in search_wire_headlines(lookback_hours):
        if (now - h.published_at).total_seconds() > lookback_hours * 3600:
            continue
        ticker = ticker_for_headline(h.title, name_map)
        if ticker:
            out.setdefault(ticker, h.title)
    return out
