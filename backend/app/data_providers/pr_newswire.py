"""Company press releases from PR Newswire's public RSS feeds.

Why: a company's own announcement (an earnings date, a deal, a new director) is
often public on a wire minutes before it reaches Yahoo's headline list, and a
press release names the company in a way that is easy to match without
guessing. Releases are saved as dated news (the same archive as every other
headline) with source "PR Newswire".

Idea from quant-mind (LLMQuant/quant-mind, MIT): collect PR Newswire releases
politely (a minimum gap between requests, retries with backoff, a size limit)
and keep the release's own timestamp as the time it became public. No code was
copied: quant-mind reads PR Newswire's HTML listing pages, this module reads the
RSS feeds PR Newswire publishes for exactly this purpose, which are far less
likely to change shape.

Terms and politeness: the feeds are public, robots.txt does not disallow them,
and the responses carry no login or paywall. Even so this module identifies
itself with a plain User-Agent, waits at least `MIN_SECONDS_BETWEEN_REQUESTS`
between requests, keeps each feed in memory for `FEED_CACHE_SECONDS`, makes a
handful of requests per run, and gives up on a feed after a few tries. It reads
only the title, link, time and the short summary the feed itself provides: it
never opens the release page.

Matching a release to a company is deliberately strict, because a wrong match
puts another company's news under a ticker you may be about to trade:
  1. an exchange tag naming the ticker: "(NASDAQ: NVDA)", "NYSE: IBM",
     "(NASDAQ: CHI, CHY and CSQ)" (a list is only read inside parentheses);
  2. the company's exact full name from the watchlist, case-sensitive, on word
     boundaries ("Apple Inc."), never a shortened or similar name.
Anything else is not matched. A release that matches nothing in the watchlist
is simply not saved.

Failure behaviour: a feed that cannot be fetched or parsed is reported and
skipped, the other feed still runs, and nothing is ever invented.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape

import httpx
from sqlmodel import Session

from app.data_providers.base import DataProviderError, NewsItem
from app.knowledge import is_simulated, to_naive_utc
from app.services.archive_service import archive_news
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

SOURCE_LABEL = "PR Newswire"
PRN_HOST_PREFIX = "https://www.prnewswire.com/"
# The all-releases feed plus the financial-services one (where public-company
# earnings and deal announcements concentrate). Each carries only the newest
# ~20 releases, so a run is a few minutes of coverage, not a backfill.
FEED_URLS: tuple[str, ...] = (
    "https://www.prnewswire.com/rss/news-releases-list.rss",
    "https://www.prnewswire.com/rss/financial-services-latest-news/financial-services-latest-news-list.rss",
)
USER_AGENT = "Strategeia/1.0 (personal research dashboard; reads public PR Newswire RSS feeds)"

REQUEST_TIMEOUT_SECONDS = 15.0
MIN_SECONDS_BETWEEN_REQUESTS = 1.0
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 20.0
# A feed is ~40 KB; anything this large is not the feed we expect.
MAX_FEED_BYTES = 3_000_000
FEED_CACHE_SECONDS = 300
# Names shorter than this are never matched by name: too likely to be a common word.
MIN_NAME_MATCH_LENGTH = 5
MAX_HEADLINE_LENGTH = 300

FetchFn = Callable[[str], str]


@dataclass(frozen=True)
class PressRelease:
    title: str
    url: str
    # The release's own time as naive UTC, or None when the feed gave none we can prove.
    published_at: datetime | None
    summary: str
    raw_published_at: str


@dataclass(frozen=True)
class ReleaseMatch:
    symbol: str
    basis: str  # "exchange_tag" | "company_name"


@dataclass
class CollectResult:
    feeds_read: int = 0
    feeds_failed: int = 0
    releases_seen: int = 0
    releases_matched: int = 0
    new: int = 0
    already_saved: int = 0
    skipped_reason: str | None = None
    failures: list[str] = field(default_factory=list)


# --- fetching (polite) -----------------------------------------------------------------

_rate_lock = threading.Lock()
_last_request_monotonic: float | None = None
_feed_cache: dict[str, tuple[float, str]] = {}
_cache_lock = threading.Lock()


def clear_feed_cache() -> None:
    """Forget cached feeds and the request pacing (tests, and a manual refresh)."""
    global _last_request_monotonic
    with _cache_lock:
        _feed_cache.clear()
    with _rate_lock:
        _last_request_monotonic = None


def _wait_for_turn() -> None:
    """Block until MIN_SECONDS_BETWEEN_REQUESTS has passed since the last request
    this process made to PR Newswire (shared by every thread)."""
    global _last_request_monotonic
    with _rate_lock:
        now = time.monotonic()
        if _last_request_monotonic is not None:
            wait = MIN_SECONDS_BETWEEN_REQUESTS - (now - _last_request_monotonic)
            if wait > 0:
                time.sleep(wait)
        _last_request_monotonic = time.monotonic()


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw and raw.strip().isdigit():
        return min(float(raw.strip()), BACKOFF_MAX_SECONDS)
    return None


def fetch_feed_text(url: str) -> str:
    """The feed's XML text, from a short in-memory cache or one polite request.
    Raises DataProviderError after the attempts run out (never returns a half-read body)."""
    with _cache_lock:
        cached = _feed_cache.get(url)
    if cached is not None and time.monotonic() - cached[0] < FEED_CACHE_SECONDS:
        return cached[1]

    last_error = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _wait_for_turn()
        delay = min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_MAX_SECONDS)
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"},
                timeout=REQUEST_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}"
        else:
            if response.status_code == 200:
                if len(response.content) > MAX_FEED_BYTES:
                    raise DataProviderError(f"PR Newswire feed is larger than {MAX_FEED_BYTES} bytes; ignored")
                text = response.text
                with _cache_lock:
                    _feed_cache[url] = (time.monotonic(), text)
                return text
            last_error = f"HTTP {response.status_code}"
            if response.status_code == 429 or response.status_code >= 500:
                delay = _retry_after_seconds(response) or delay
            else:
                break  # 4xx other than 429: retrying will not change the answer
        if attempt < MAX_ATTEMPTS:
            time.sleep(delay)
    raise DataProviderError(f"PR Newswire feed {url} could not be fetched ({last_error})")


# --- parsing (tolerant) ----------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]*>")


def _plain_text(markup: str) -> str:
    return " ".join(unescape(_TAG_RE.sub(" ", markup or "")).split())


def _parse_pub_date(raw: str) -> datetime | None:
    """The release's time as naive UTC, only when it carries its own offset."""
    if not raw or not raw.strip():
        return None
    try:
        parsed = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return to_naive_utc(parsed)


def parse_feed(xml_text: str) -> list[PressRelease]:
    """Every usable <item> in an RSS document. An item with no title, or a link
    that is not on prnewswire.com, is skipped; a broken document raises ValueError.

    A DOCTYPE or entity declaration is refused outright: a plain RSS feed has
    neither, and refusing them rules out entity-expansion tricks."""
    if not xml_text or not xml_text.strip():
        raise ValueError("empty feed")
    head = xml_text[:2000].lower()
    if "<!doctype" in head or "<!entity" in xml_text.lower():
        raise ValueError("feed declares a DOCTYPE or entity; refused")
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"feed is not valid XML: {exc}") from exc

    releases: list[PressRelease] = []
    for item in root.iter("item"):
        title = _plain_text(item.findtext("title") or "")[:MAX_HEADLINE_LENGTH]
        link = (item.findtext("link") or item.findtext("guid") or "").strip()
        if not title or not link.startswith(PRN_HOST_PREFIX):
            continue
        raw_date = (item.findtext("pubDate") or "").strip()
        releases.append(
            PressRelease(
                title=title,
                url=link,
                published_at=_parse_pub_date(raw_date),
                summary=_plain_text(item.findtext("description") or ""),
                raw_published_at=raw_date,
            )
        )
    return releases


# --- matching (strict) -----------------------------------------------------------------

_EXCHANGE = r"(?:NYSE(?:\s+(?:American|Arca|MKT))?|NASDAQ(?:\s+(?:GS|GM|CM))?|Nasdaq(?:\s+(?:GS|GM|CM))?|AMEX)"
_TICKER = r"[A-Z]{1,5}(?:[.\-][A-Z])?"
_TICKER_RE = re.compile(rf"^{_TICKER}$")
# "(NASDAQ: CHI, CHY and CSQ)": a parenthesised tag, which may list several tickers.
_PAREN_TAG_RE = re.compile(rf"\(\s*{_EXCHANGE}\s*:\s*([^()]{{1,120}})\)")
# "NYSE: IBM" or "NYSE:STAG" outside parentheses: the first ticker only.
_BARE_TAG_RE = re.compile(rf"\b{_EXCHANGE}\s*:\s*({_TICKER})\b")
_LIST_SPLIT_RE = re.compile(r"\s*(?:,|\band\b|&|\|)\s*")


def _normalise_ticker(raw: str) -> str:
    return raw.upper().replace(".", "-")


def tickers_in_text(text: str) -> set[str]:
    """Tickers a release names with an exchange tag. Share-class dots become
    dashes ("BRK.B" -> "BRK-B") to match the app's symbols."""
    found: set[str] = set()
    for match in _PAREN_TAG_RE.finditer(text):
        for token in _LIST_SPLIT_RE.split(match.group(1).strip()):
            token = token.strip()
            if _TICKER_RE.fullmatch(token):
                found.add(_normalise_ticker(token))
            else:
                break  # the list ended (e.g. followed by prose): stop, never guess
    for match in _BARE_TAG_RE.finditer(text):
        found.add(_normalise_ticker(match.group(1)))
    return found


def match_release(
    release: PressRelease, symbols: Iterable[str], names: Mapping[str, str] | None = None
) -> list[ReleaseMatch]:
    """Which of `symbols` this release is about, by the two strict rules above.
    Each symbol appears at most once (an exchange tag outranks a name match)."""
    wanted = {s.strip().upper() for s in symbols if s and s.strip()}
    text = f"{release.title}\n{release.summary}"
    matches: dict[str, str] = {}
    for ticker in tickers_in_text(text):
        if ticker in wanted:
            matches[ticker] = "exchange_tag"
    for symbol in wanted - matches.keys():
        name = ((names or {}).get(symbol) or "").strip()
        if len(name) >= MIN_NAME_MATCH_LENGTH and re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text):
            matches[symbol] = "company_name"
    return [ReleaseMatch(symbol=s, basis=b) for s, b in sorted(matches.items())]


# --- collecting ------------------------------------------------------------------------


def _universe_names() -> dict[str, str]:
    from app.data_providers import universe

    return {e.symbol.upper(): e.name for e in universe.load_universe() if e.name}


def collect_press_releases(
    session: Session,
    symbols: Iterable[str],
    *,
    fetch: FetchFn | None = None,
    names: Mapping[str, str] | None = None,
    feed_urls: Iterable[str] = FEED_URLS,
    fetched_at: datetime | None = None,
) -> CollectResult:
    """Read the feeds once and save every release that matches one of `symbols`
    as dated news (source "PR Newswire"). Safe to call from a watcher: it never
    raises, and a failed feed is reported in `failures` while the rest still runs.

    known_at is the release's own timestamp (the archive never lets it be later
    than the fetch time); a release whose time we cannot prove is saved with the
    fetch time instead. Crypto pairs ("-USD") are never matched: they issue no
    press releases and a ticker tag of "BTC" means nothing here.
    """
    result = CollectResult()
    if is_simulated():
        result.skipped_reason = "A backtest is simulating a past moment; nothing is fetched or saved."
        return result
    wanted = [s.strip().upper() for s in symbols if s and s.strip() and not s.strip().upper().endswith("-USD")]
    if not wanted:
        result.skipped_reason = "No symbols to match press releases against."
        return result
    try:
        if session.new or session.dirty or session.deleted:
            result.skipped_reason = "The database session has uncommitted changes; skipped."
            return result
        name_map = names if names is not None else _universe_names()
        reader = fetch or fetch_feed_text
        fetched = to_naive_utc(fetched_at) if fetched_at is not None else utcnow_naive()

        seen_urls: set[str] = set()
        for url in feed_urls:
            try:
                releases = parse_feed(reader(url))
            except (DataProviderError, ValueError, httpx.HTTPError) as exc:
                result.feeds_failed += 1
                result.failures.append(f"{url}: {exc}")
                logger.warning("PR Newswire feed %s skipped: %s", url, exc)
                continue
            result.feeds_read += 1
            for release in releases:
                if release.url in seen_urls:
                    continue
                seen_urls.add(release.url)
                result.releases_seen += 1
                matches = match_release(release, wanted, name_map)
                if not matches:
                    continue
                result.releases_matched += 1
                for match in matches:
                    saved = archive_news(
                        session,
                        match.symbol,
                        [NewsItem(headline=release.title, source=SOURCE_LABEL, url=release.url,
                                  published_at=release.raw_published_at)],
                        SOURCE_LABEL,
                        fetched_at=fetched,
                    )
                    result.new += saved.new
                    result.already_saved += saved.already_saved
    except Exception as exc:  # noqa: BLE001 - a collector must not break its caller
        logger.warning("PR Newswire collection failed", exc_info=True)
        result.failures.append(f"collection stopped: {type(exc).__name__}")
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            logger.debug("rollback after a failed collection also failed", exc_info=True)
    return result
