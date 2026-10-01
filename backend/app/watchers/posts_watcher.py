"""The posts watcher: Donald Trump's Truth Social posts, from an unofficial archive.

Every poll reads the archive feed (`data_providers/posts_feed.py`), stores each post
as a dated fact (known_at = the post's own time), and only then returns events. The
runner records the events after that, so the facts exist whatever happens to an alert.

Which posts become events is decided by rules alone (`analysis/post_topics.py`);
there is no AI here, and an AI may only ever extract facts from words, never decide
what they mean:

  * a post that names a watchlist company (its exact name, or a ticker in an
    unmistakable form): one event per named company. This is the only kind of
    event that may start an ordinary full evaluation of the symbol, like any other
    watcher event (queued for the open when the market is shut). The post does not
    pick a direction: whether it is good or bad news for the company is not read.
  * a post that touches a market topic (tariffs, China, the Fed, chips, oil, drug
    makers, banks, electric vehicles, crypto) but names no watchlist company: one
    market-wide, alert-only event.

Reposts ("RT @...") and posts with no words (an image or a video) are stored and
never alert. A post older than EVENT_MAX_AGE is stored but never alerts, so the
first run on an empty database does not announce yesterday.

Honest limits, repeated in the documentation: a post can move a stock within
minutes, and the watcher polls every five minutes and the scans run three times a
day, so most of the move is usually over by the time anything here reacts. The
source is an unofficial third-party archive that can lag, drop posts or stop. If
the archive cannot be read, or its format changes so that no post can be read, the
poll raises (the runner records the error and backs off) and produces no events.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from app.analysis.post_topics import MARKET_WIDE_TOPICS, SymbolMatcher, match_symbols, match_topics, watchlist_matchers
from app.data_providers.feed_http import fetch_feed
from app.data_providers.posts_feed import Post, fetch_posts, ingest_post
from app.watchers.base import Watcher, WatcherContext, WatcherEvent
from app.watchers.registry import get_watcher, register_watcher

logger = logging.getLogger(__name__)

WATCHER_NAME = "trump_posts"

# Posts arrive in bursts and move markets in minutes: poll as often as the shared
# scheduler tick allows, which is still gentle on a small public feed.
POLL_INTERVAL_SECONDS = 5 * 60
# A flurry of posts about one company or one topic gets one alert per half hour; the
# rest are recorded. (Per company, and one shared window for all market-wide topics.)
COOLDOWN_SECONDS = 30 * 60
DAILY_FIRE_CAP = 30
# A post older than this is stored but never alerts: it is old news by now.
EVENT_MAX_AGE = timedelta(hours=6)
# How much of the post is quoted in an alert's headline.
SNIPPET_LENGTH = 160


def _snippet(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SNIPPET_LENGTH else text[: SNIPPET_LENGTH - 1].rstrip() + "…"


def events_for_post(post: Post, matchers: tuple[SymbolMatcher, ...]) -> list[WatcherEvent]:
    """The events one post deserves (none for a repost, an empty post, or one that
    matches nothing). Pure: rules only; the age check belongs to the watcher."""
    if post.is_repost or not post.has_text:
        return []
    topics = match_topics(post.text)
    symbols = match_symbols(post.text, matchers)
    quote = _snippet(post.text)
    details = {
        "post_id": post.id,
        "topics": topics,
        "symbols": symbols,
        "text": quote,
        "archive_url": post.archive_url,
        "unofficial_source": True,
    }
    names = {m.symbol: m.name for m in matchers}
    if symbols:
        topic_note = f" (also: {', '.join(topics)})" if topics else ""
        return [
            WatcherEvent(
                watcher=WATCHER_NAME,
                symbol=symbol,
                kind="post_names_company",
                headline=f"{symbol}: a Trump post names {names.get(symbol) or symbol}{topic_note}: “{quote}”",
                known_at=post.published,
                source_ref=post.url,
                severity="notable",
                details=details,
            )
            for symbol in symbols
        ]
    if topics:
        wide = any(t in MARKET_WIDE_TOPICS for t in topics)
        return [
            WatcherEvent(
                watcher=WATCHER_NAME,
                symbol=None,
                kind="post_topic",
                headline=f"A Trump post touches {', '.join(topics)}: “{quote}”",
                known_at=post.published,
                source_ref=post.url,
                severity="notable" if wide else "info",
                details=details,
            )
        ]
    return []


class PostsWatcher(Watcher):
    name = WATCHER_NAME
    description = (
        "Donald Trump's Truth Social posts, read from an unofficial third-party archive (it can lag or stop). "
        "Posts naming a watchlist company can start an evaluation of it; market topics (tariffs, China, the Fed, "
        "chips, oil, banks, crypto...) only alert. Rules decide, no AI."
    )
    poll_interval_seconds = POLL_INTERVAL_SECONDS
    cooldown_seconds = COOLDOWN_SECONDS
    daily_fire_cap = DAILY_FIRE_CAP

    def __init__(
        self,
        *,
        fetch: Callable[[str], bytes] = fetch_feed,
        matchers: Callable[[], tuple[SymbolMatcher, ...]] = watchlist_matchers,
    ) -> None:
        self._fetch = fetch
        self._matchers = matchers

    def poll(self, context: WatcherContext) -> list[WatcherEvent]:
        posts = fetch_posts(self._fetch)
        matchers = self._matchers()
        events: list[WatcherEvent] = []
        for post in posts:
            ingest_post(context.session, post)
            if context.now - post.published > EVENT_MAX_AGE:
                continue
            events.extend(events_for_post(post, matchers))
        return events


def register_posts_watcher() -> None:
    """Install the watcher once. Called at application start-up, not at import."""
    if get_watcher(WATCHER_NAME) is None:
        register_watcher(PostsWatcher())
