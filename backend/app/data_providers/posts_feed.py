"""Donald Trump's Truth Social posts, from an UNOFFICIAL archive feed, as dated facts.

Truth Social itself refuses scripts (HTTP 403), so the posts are read from
trumpstruth.org, a third-party archive that republishes them as an RSS feed. That
makes this source unofficial in every sense and the app says so wherever it shows
a post: the archive can lag behind the real post, can drop or edit items, can change
its format, or can stop altogether. A post's time here is the time the archive gives
(RFC 822 with a zone), which in a live check matched the posting time to the second,
but nothing guarantees that. Its posts are public statements, stored as the archive
gives them and never checked against Truth Social.

The feed lists the newest ~100 posts (a day or two at the current pace). His older
tweets (2009 to 2021) are archived by thetrumparchive.com as a JSON export; loading
them is out of scope here because they say nothing about the live feed's timing.

What is stored: one `post` fact per post, market-wide (no symbol: which companies a
post names is worked out by rules when it is read, see analysis/post_topics.py), with

  known_at     = the post time from the feed, converted to UTC (basis "source")
  effective_at = the same moment (a post is about itself)
  payload      = id, text, url (the Truth Social link), archive_url, is_repost,
                 has_text

No AI reads a post. A language model may only ever extract facts from a post's
words; what a post means for a stock is decided by rules, and this module does
neither.
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session

from app.data_providers.base import DataProviderError
from app.data_providers.fed_feed import parse_pub_date
from app.data_providers.feed_http import fetch_feed
from app.knowledge import FactKind, RecordedFact, make_dedupe_key, record_fact

logger = logging.getLogger(__name__)

SOURCE_NAME = "trumpstruth.org (unofficial archive)"
DEDUPE_PREFIX = "post"
FEED_URL = "https://www.trumpstruth.org/feed"
_TRUTH_NS = "{https://truthsocial.com/ns}"

_FORBIDDEN_XML_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_BLOCK_TAG_RE = re.compile(r"</?(?:p|br)\b[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_REPOST_RE = re.compile(r"^RT\b")
_NO_TITLE_RE = re.compile(r"^\[No Title\]")
_STATUS_ID_RE = re.compile(r"/(\d+)/?$")


@dataclass(frozen=True)
class Post:
    id: str
    text: str  # plain text; "" for a post with no words (an image or video)
    url: str  # the Truth Social link when the archive gives one, else the archive's own
    archive_url: str
    published: datetime  # naive UTC
    is_repost: bool

    @property
    def has_text(self) -> bool:
        return bool(self.text)


def html_to_text(raw: str | None) -> str:
    """Plain text of a post's HTML: paragraph and line breaks become spaces, every
    other tag is dropped, entities are decoded, whitespace collapsed."""
    if not raw:
        return ""
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub("", _BLOCK_TAG_RE.sub(" ", raw)))).strip()


def parse_posts_feed(xml: bytes) -> list[Post]:
    """Posts of the archive feed, newest first as listed. An item with no usable id
    or time is skipped; a body that is not XML raises DataProviderError."""
    if _FORBIDDEN_XML_RE.search(xml):
        raise DataProviderError("posts feed contained a DOCTYPE or ENTITY declaration; refused")
    try:
        root = ET.fromstring(xml.decode("utf-8-sig").encode("utf-8"))
    except (ET.ParseError, UnicodeDecodeError) as exc:
        raise DataProviderError(f"posts feed was not valid XML: {exc}") from exc
    posts: list[Post] = []
    for node in root.iter("item"):
        archive_url = (node.findtext("link") or node.findtext("guid") or "").strip()
        post_id = (node.findtext(f"{_TRUTH_NS}originalId") or "").strip()
        if not post_id:
            found = _STATUS_ID_RE.search(archive_url)
            post_id = found[1] if found else ""
        published = parse_pub_date(node.findtext("pubDate"))
        if not post_id or published is None:
            logger.warning("posts feed: skipped an item without a usable id or time (%r)", archive_url[:80])
            continue
        title = html_to_text(node.findtext("title"))
        text = html_to_text(node.findtext("description"))
        if not text and not _NO_TITLE_RE.match(title):
            text = title
        if _NO_TITLE_RE.match(text):
            text = ""
        url = (node.findtext(f"{_TRUTH_NS}originalUrl") or "").strip() or archive_url
        posts.append(
            Post(
                id=post_id,
                text=text,
                url=url,
                archive_url=archive_url,
                published=published,
                is_repost=bool(_REPOST_RE.match(text)),
            )
        )
    return posts


def ingest_post(session: Session, post: Post, *, commit: bool = True) -> RecordedFact:
    """Store one post as a dated fact. Safe to repeat: the post's id is the key."""
    return record_fact(
        session,
        kind=FactKind.POST,
        source=SOURCE_NAME,
        symbol=None,
        source_ref=post.url,
        dedupe_key=make_dedupe_key(DEDUPE_PREFIX, post.id),
        known_at=post.published,
        effective_at=post.published,
        payload={
            "id": post.id,
            "text": post.text,
            "url": post.url,
            "archive_url": post.archive_url,
            "is_repost": post.is_repost,
            "has_text": post.has_text,
        },
        commit=commit,
    )


FetchFeed = Callable[[str], bytes]


def fetch_posts(fetch: FetchFeed = fetch_feed) -> list[Post]:
    """Download and parse the feed, oldest post first. Raises DataProviderError when
    the download fails or the feed holds no readable post at all (an archive that
    changed its format looks exactly like that, and it must not pass as "quiet")."""
    posts = parse_posts_feed(fetch(FEED_URL))
    if not posts:
        raise DataProviderError("the posts feed held no readable post (its format may have changed)")
    posts.sort(key=lambda p: (p.published, p.id))
    return posts


@dataclass
class PostsBackfillReport:
    posts_seen: int = 0
    created: int = 0
    already_stored: int = 0
    errors: list[str] = field(default_factory=list)


def backfill_posts(session: Session, fetch: FetchFeed = fetch_feed) -> PostsBackfillReport:
    """Store everything the feed currently lists (its newest ~100 posts). Re-running
    is harmless: posts already stored are counted, not stored again. A failed
    download is reported in `errors` and nothing is stored."""
    report = PostsBackfillReport()
    try:
        posts = fetch_posts(fetch)
    except DataProviderError as exc:
        report.errors.append(str(exc))
        return report
    report.posts_seen = len(posts)
    for post in posts:
        if ingest_post(session, post).created:
            report.created += 1
        else:
            report.already_stored += 1
    return report
