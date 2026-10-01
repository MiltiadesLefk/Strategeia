"""The posts feed, the topic and company rules, the posts watcher and the
post_mentions silent signal.

The feed excerpt is a saved copy of the real archive feed (tests/fixtures/posts_feed);
nothing touches the network. The reference moment is the evening of 1 Oct 2026.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis import post_mentions
from app.analysis.post_topics import build_matcher, build_matchers, core_name, match_symbols, match_topics
from app.analysis.shadow_signals import ShadowContext, evaluate_shadow_signals, registered_shadow_signals
from app.config import AppSettings
from app.data_providers.base import DataProviderError
from app.data_providers.posts_feed import (
    Post,
    backfill_posts,
    fetch_posts,
    html_to_text,
    ingest_post,
    parse_posts_feed,
)
from app.knowledge import FactKind, KnownFact, as_of, facts_known_as_of
from app.watchers import registry
from app.watchers.base import Watcher
from app.watchers.models import WatcherState
from app.watchers.posts_watcher import PostsWatcher, events_for_post, register_posts_watcher
from app.watchers.reevaluate import ReevaluationOutcome
from app.watchers.runner import run_watcher_once

FIXTURE = Path(__file__).parent / "fixtures" / "posts_feed" / "trump_feed.xml"
NOW = datetime(2026, 10, 1, 20, 0)
# Names as the watchlist spells them.
WATCHLIST = [
    ("BA", "Boeing Co."),
    ("AAPL", "Apple Inc."),
    ("NVDA", "NVIDIA Corporation"),
    ("TGT", "Target Corporation"),
    ("V", "Visa Inc."),
    ("F", "Ford Motor Company"),
    ("GM", "General Motors"),
    ("DOW", "Dow Inc."),
    ("FOXA", "Fox Corporation"),
    ("AMZN", "Amazon.com, Inc."),
    ("BTC-USD", "Bitcoin"),
]
MATCHERS = build_matchers(WATCHLIST)


def fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def post(text: str, minutes_ago: int = 30, post_id: str = "1", **kw) -> Post:
    return Post(
        id=post_id,
        text=text,
        url=f"https://truthsocial.com/@realDonaldTrump/{post_id}",
        archive_url=f"https://www.trumpstruth.org/statuses/{post_id}",
        published=NOW - timedelta(minutes=minutes_ago),
        is_repost=kw.get("is_repost", False),
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class Spy:
    def __init__(self):
        self.alerts: list[str] = []

    def __call__(self, token, chat, text):
        self.alerts.append(text)


class Reevals:
    def __init__(self):
        self.symbols: list[str] = []

    def __call__(self, session, settings, event, now, data_provider, llm_provider, *, clock=None):
        self.symbols.append(event.symbol)
        return ReevaluationOutcome("evaluated", f"{event.symbol} was evaluated.")


def run(session, watcher, now=NOW, notifier=None, reevaluator=None):
    return run_watcher_once(
        session,
        watcher,
        AppSettings(watchers_enabled=True, watchers_action="alert_and_reevaluate", telegram_bot_token="t", telegram_chat_id="c"),
        now,
        data_provider=object(),
        llm_provider=object(),
        notifier=notifier or Spy(),
        reevaluator=reevaluator or Reevals(),
    )


def feed_of(*posts: Post) -> bytes:
    """A minimal feed in the archive's shape holding the given posts."""
    items = ""
    for p in posts:
        items += f"""<item><title><![CDATA[{p.text}]]></title><link>{p.archive_url}</link>
        <description><![CDATA[<p>{p.text}</p>]]></description><guid>{p.archive_url}</guid>
        <pubDate>{p.published.strftime('%a, %d %b %Y %H:%M:%S')} +0000</pubDate>
        <truth:originalUrl>{p.url}</truth:originalUrl><truth:originalId>{p.id}</truth:originalId></item>"""
    return (
        '<?xml version="1.0"?><rss version="2.0" xmlns:truth="https://truthsocial.com/ns"><channel>'
        f"{items}</channel></rss>"
    ).encode()


def stored_posts(session):
    return facts_known_as_of(session, FactKind.POST, as_of=datetime(2030, 1, 1))


# ------------------------------------------------------------------ parsing


def test_the_feed_parses_with_utc_times_ids_and_urls():
    posts = parse_posts_feed(fixture_bytes())
    assert len(posts) == 9
    first = posts[0]
    assert first.published == datetime(2026, 10, 1, 18, 38, 20)  # "+0000" in the feed
    assert first.published.tzinfo is None
    assert first.id == "117367105128831509"
    assert first.url == "https://truthsocial.com/@realDonaldTrump/117367105128831509"
    assert first.archive_url == "https://www.trumpstruth.org/statuses/42072"
    assert first.text.startswith("Prices are way down") and not first.is_repost and first.has_text


def test_reposts_and_wordless_posts_are_marked():
    posts = parse_posts_feed(fixture_bytes())
    wordless = posts[1]
    assert wordless.text == "" and not wordless.has_text and not wordless.is_repost
    reposts = [p for p in posts if p.is_repost]
    assert len(reposts) == 1 and "Boeing" in reposts[0].text
    # the same words posted by himself are not a repost
    own = [p for p in posts if "Boeing" in p.text and not p.is_repost]
    assert len(own) == 1


def test_html_becomes_plain_text():
    assert html_to_text("<p>One &amp; two</p><p>three<br />four</p>") == "One & two three four"
    assert html_to_text("<p>RT <span><a href='x'>@<span>name</span></a></span>Hello</p>") == "RT @nameHello"
    assert html_to_text(None) == "" and html_to_text("<p></p>") == ""


def test_items_without_an_id_or_a_zoned_time_are_skipped():
    xml = b"""<?xml version="1.0"?><rss xmlns:truth="https://truthsocial.com/ns"><channel>
      <item><title>ok</title><link>https://www.trumpstruth.org/statuses/7</link><description>&lt;p&gt;ok&lt;/p&gt;</description>
        <pubDate>Thu, 01 Oct 2026 18:38:20 +0000</pubDate></item>
      <item><title>no time</title><link>https://www.trumpstruth.org/statuses/8</link></item>
      <item><title>zoneless</title><link>https://www.trumpstruth.org/statuses/9</link><pubDate>Thu, 01 Oct 2026 18:38:20</pubDate></item>
      <item><title>no id</title><link>https://elsewhere.test/x</link><pubDate>Thu, 01 Oct 2026 18:38:20 +0000</pubDate></item>
    </channel></rss>"""
    [only] = parse_posts_feed(xml)
    assert only.id == "7"  # the archive's own status number when no original id is given
    assert only.url == only.archive_url


def test_a_body_that_is_not_xml_raises():
    with pytest.raises(DataProviderError):
        parse_posts_feed(b"<html><body>blocked")
    with pytest.raises(DataProviderError, match="DOCTYPE"):
        parse_posts_feed(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss/>')


def test_a_feed_with_no_readable_post_is_an_error_not_a_quiet_day():
    with pytest.raises(DataProviderError, match="no readable post"):
        fetch_posts(lambda url: b'<?xml version="1.0"?><rss><channel></channel></rss>')


# ------------------------------------------------------------------ dated facts


def test_ingest_stores_a_post_known_at_its_post_time(session):
    first = parse_posts_feed(fixture_bytes())[0]
    recorded = ingest_post(session, first)
    fact = recorded.fact
    assert recorded.created
    assert fact.kind == "post" and fact.symbol is None
    assert fact.known_at == datetime(2026, 10, 1, 18, 38, 20) and fact.known_at_basis == "source"
    assert fact.effective_at == fact.known_at
    assert fact.source == "trumpstruth.org (unofficial archive)"
    assert fact.payload["id"] == first.id and fact.payload["text"].startswith("Prices are way down")
    assert fact.payload["is_repost"] is False


def test_ingest_is_idempotent_and_backfill_resumes(session):
    first = backfill_posts(session, lambda url: fixture_bytes())
    assert first.posts_seen == 9 and first.created == 9 and first.errors == []
    second = backfill_posts(session, lambda url: fixture_bytes())
    assert second.created == 0 and second.already_stored == 9
    assert len(session.exec(select(KnownFact)).all()) == 9


def test_a_failed_backfill_stores_nothing_and_says_so(session):
    def blocked(url):
        raise DataProviderError("feed fetch failed: HTTP 403")

    report = backfill_posts(session, blocked)
    assert report.created == 0 and "HTTP 403" in report.errors[0]
    assert stored_posts(session) == []


def test_a_backtest_cannot_see_a_post_made_after_its_moment(session):
    backfill_posts(session, lambda url: fixture_bytes())
    moment = datetime(2026, 10, 1, 18, 0)
    with as_of(moment):
        visible = facts_known_as_of(session, FactKind.POST)
    assert visible and all(f.known_at <= moment for f in visible)
    assert not any(f.payload["text"].startswith("Prices are way down") for f in visible)


# ------------------------------------------------------------------ the rules


@pytest.mark.parametrize(
    "text, topics",
    [
        ("We will put tariffs on everything", ["tariffs"]),
        ("TARIFF day is coming", ["tariffs"]),
        ("Talks with President Xi of China went well", ["china"]),
        ("The Chinese economy is struggling", ["china"]),
        ("Jerome Powell should cut interest rates NOW", ["fed_rates"]),
        ("The Fed is too late", ["fed_rates"]),
        ("The Federal Reserve Board", ["fed_rates"]),
        ("America's best semiconductor makers", ["semiconductors"]),
        ("Advanced chips are being built here", ["semiconductors"]),
        ("Oil prices are way down and LNG exports are up", ["oil_gas"]),
        ("Big Pharma must lower drug prices", ["pharma"]),
        ("The banks are doing great", ["banks"]),
        ("An EV mandate is a disaster", ["electric_vehicles"]),
        ("Bitcoin and crypto are the future", ["crypto"]),
        ("Tariffs on China chips and oil", ["tariffs", "china", "oil_gas"]),
        # precision: ordinary words that look like topics
        ("We fed the people and they loved it", []),
        ("Potato chips are great, so is the chip on his shoulder", []),
        ("The Strait is closed and the soil is rich", []),
        ("Chinatown was lovely and I like porcelain", []),
        ("This river bank is pretty", ["banks"]),  # a known false positive: bank is bank; alert-only anyway
        ("I prefer the ev of the evening", []),
        ("Prices are way down from what Biden left us", []),
    ],
)
def test_topic_matching(text, topics):
    assert match_topics(text) == topics


@pytest.mark.parametrize(
    "text, symbols",
    [
        ("Congratulations to the GREAT Boeing Company for producing a plane", ["BA"]),
        ("BOEING is back!", ["BA"]),
        ("Apple's Tim Cook called me today", ["AAPL"]),
        ("I eat an apple a day", []),  # lower case: the fruit
        ("Great news from General Motors and Ford Motor Company", ["F", "GM"]),
        ("$NVDA is flying", ["NVDA"]),
        ("(NASDAQ: NVDA) announced", ["NVDA"]),
        ("NVDA reached a record", ["NVDA"]),
        ("Our Target is clear and the Visa will be granted", []),  # ordinary words, not companies
        ("Target Corporation reported results", ["TGT"]),
        ("Visa Inc. sued", ["V"]),
        ("Fox News is fake and the Dow hit a record", []),
        ("Fox Corporation is mine", ["FOXA"]),
        ("Amazon is paying nothing", ["AMZN"]),
        ("Bitcoin is up", []),  # crypto is a topic, never a named company
        ("The F-35 is great, T for Tuesday, ALL OF THEM, a fox", []),  # short tickers are never matched bare
        ("A great aircraft maker", []),
        ("Boeingly", []),  # not a whole word
    ],
)
def test_company_matching_is_exact_names_and_strict_tickers_only(text, symbols):
    assert sorted(match_symbols(text, MATCHERS)) == sorted(symbols)


def test_core_names_drop_legal_endings_only():
    assert core_name("Boeing Co.") == "Boeing"
    assert core_name("Ford Motor Company") == "Ford Motor"
    assert core_name("Amazon.com, Inc.") == "Amazon.com"
    assert core_name("Hartford (The)") == "Hartford"
    assert core_name("General Motors") == "General Motors"
    assert core_name("Inc.") == "Inc"  # never reduced to nothing


def test_unmatchable_symbols_get_no_matcher():
    assert build_matcher("BTC-USD", "Bitcoin") is None
    assert build_matcher("^VIX", "Volatility") is None
    # a symbol with no real name keeps only its strict ticker forms
    matcher = build_matcher("ZZZZ", "ZZZZ")
    assert match_symbols("ZZZZ is mentioned", (matcher,)) == ["ZZZZ"]


# ------------------------------------------------------------------ event rules


def test_a_post_naming_a_company_is_one_event_per_company():
    events = events_for_post(post("Boeing and Apple are doing great, and tariffs help"), MATCHERS)
    assert sorted(e.symbol for e in events) == ["AAPL", "BA"]
    for e in events:
        assert e.kind == "post_names_company" and e.severity == "notable"
        assert e.details["topics"] == ["tariffs"] and sorted(e.details["symbols"]) == ["AAPL", "BA"]
        assert e.details["unofficial_source"] is True
        assert e.source_ref == "https://truthsocial.com/@realDonaldTrump/1"
        assert e.known_at == NOW - timedelta(minutes=30)
    assert "tariffs" in events[0].headline


def test_a_topic_without_a_company_is_one_market_wide_event():
    [event] = events_for_post(post("Tariffs on China are coming"), MATCHERS)
    assert event.symbol is None and event.kind == "post_topic" and event.severity == "notable"
    assert event.details["topics"] == ["tariffs", "china"] and event.details["symbols"] == []
    [sector] = events_for_post(post("Drill baby drill: oil is back"), MATCHERS)
    assert sector.symbol is None and sector.severity == "info"


@pytest.mark.parametrize(
    "item",
    [
        post("Prices are way down and we won the election"),
        post("RT @someone Boeing is great", is_repost=True),
        post(""),
    ],
)
def test_posts_that_match_nothing_or_are_reposts_make_no_event(item):
    assert events_for_post(item, MATCHERS) == []


def test_the_saved_feed_gives_the_expected_events():
    events = [e for p in parse_posts_feed(fixture_bytes()) for e in events_for_post(p, MATCHERS)]
    by_kind = {(e.kind, e.symbol) for e in events}
    assert ("post_names_company", "BA") in by_kind  # his own Boeing post, not the repost
    assert ("post_topic", None) in by_kind  # the Powell / Federal Reserve post
    assert len([e for e in events if e.symbol == "BA"]) == 1
    topic = [e for e in events if e.kind == "post_topic"]
    assert any("fed_rates" in e.details["topics"] for e in topic)


# ------------------------------------------------------------------ the watcher


def watcher(*posts: Post | bytes, matchers=MATCHERS):
    body = posts[0] if posts and isinstance(posts[0], bytes) else feed_of(*posts)
    return PostsWatcher(fetch=lambda url: body, matchers=lambda: matchers)


def test_a_poll_stores_every_post_and_alerts_on_fresh_matches(session):
    spy, reevals = Spy(), Reevals()
    result = run(
        session,
        watcher(post("Boeing is great!", 20, "10"), post("Nothing about markets here", 15, "11"), post("The Fed is wrong", 10, "12")),
        notifier=spy,
        reevaluator=reevals,
    )
    assert result.error is None and result.new_events == 2 and result.fired == 2
    assert len(stored_posts(session)) == 3  # all stored
    assert reevals.symbols == ["BA"]  # the named company is evaluated; the Fed topic is not
    assert len(spy.alerts) == 2


def test_a_topic_event_never_starts_an_evaluation(session):
    reevals = Reevals()
    result = run(session, watcher(post("Tariffs on China chips", 5)), reevaluator=reevals)
    assert result.fired == 1 and reevals.symbols == []


def test_an_old_post_is_stored_but_never_alerts(session):
    result = run(session, watcher(post("Boeing is great", 6 * 60 + 5)))
    assert result.new_events == 0 and result.fired == 0
    assert len(stored_posts(session)) == 1


def test_a_repeat_poll_does_not_fire_again(session):
    w = watcher(post("Boeing is great", 20))
    first = run(session, w)
    second = run(session, w, now=NOW + timedelta(minutes=6))
    assert first.fired == 1
    assert second.duplicates == 1 and second.fired == 0


def test_a_flurry_about_one_company_is_throttled_by_the_cooldown(session):
    spy, reevals = Spy(), Reevals()
    result = run(
        session,
        watcher(post("Boeing is great", 25, "20"), post("Boeing again, so great", 20, "21"), post("Boeing! Boeing!", 15, "22")),
        notifier=spy,
        reevaluator=reevals,
    )
    assert result.new_events == 3 and result.fired == 1 and result.suppressed == 2
    assert reevals.symbols == ["BA"] and len(spy.alerts) == 1


def test_the_daily_cap_applies_through_the_framework(session):
    class Capped(PostsWatcher):
        daily_fire_cap = 2

    posts = [post(f"{name} is great", 30 - i, str(30 + i)) for i, name in enumerate(["Boeing", "Apple", "General Motors"])]
    result = run(session, Capped(fetch=lambda url: feed_of(*posts), matchers=lambda: MATCHERS))
    assert result.fired == 2 and result.suppressed == 1


def test_a_blocked_feed_is_a_recorded_error_with_no_events(session):
    def blocked(url):
        raise DataProviderError("feed fetch failed for https://www.trumpstruth.org/feed: HTTP 403")

    result = run(session, PostsWatcher(fetch=blocked, matchers=lambda: MATCHERS))
    assert result.ran and result.new_events == 0 and result.fired == 0
    assert "HTTP 403" in result.error
    state = session.get(WatcherState, "trump_posts")
    assert state.consecutive_failures == 1 and "HTTP 403" in state.last_error
    assert stored_posts(session) == []


def test_a_timeout_is_a_recorded_error_too(session):
    import httpx

    from app.data_providers.feed_http import fetch_feed

    def timing_out(url, headers, timeout):
        raise httpx.ReadTimeout("timed out")

    result = run(session, PostsWatcher(fetch=lambda url: fetch_feed(url, http_get=timing_out), matchers=lambda: MATCHERS))
    assert "ReadTimeout" in result.error and result.fired == 0


def test_a_feed_whose_format_changed_is_an_error(session):
    result = run(session, PostsWatcher(fetch=lambda url: b"<rss><channel></channel></rss>", matchers=lambda: MATCHERS))
    assert "no readable post" in result.error


def test_the_watcher_is_registered_once():
    registry.unregister_watcher("trump_posts")
    try:
        register_posts_watcher()
        register_posts_watcher()
        found = registry.get_watcher("trump_posts")
        assert isinstance(found, Watcher) and found.poll_interval_seconds == 5 * 60
    finally:
        registry.unregister_watcher("trump_posts")


# ------------------------------------------------------------------ the silent signal


@pytest.fixture
def universe(monkeypatch):
    from app.data_providers import universe as universe_module
    from app.data_providers.universe import UniverseEntry

    monkeypatch.setattr(
        universe_module, "load_universe", lambda: [UniverseEntry(s, n, "x") for s, n in WATCHLIST]
    )


def store(session, *posts: Post):
    for p in posts:
        ingest_post(session, p)


def test_a_mention_in_the_last_day_is_a_penalty_only_caution_flag(session, universe):
    assert "post_mentions" in registered_shadow_signals()
    store(session, post("Boeing is great", 120, "1"), post("Boeing again", 60, "2"), post("Apple too", 30, "3"))
    signal = post_mentions.build_post_mentions_signal(session, "BA", NOW)
    assert signal.available and signal.value == "2" and signal.would_score == -1
    assert "unofficial" in signal.reason
    # it does not depend on the trade's direction
    with as_of(NOW):
        for direction in ("long", "short", None):
            [found] = [s for s in evaluate_shadow_signals(ShadowContext("BA", direction, session)) if s.name == "post_mentions"]
            assert found.would_score == -1 and found.value == "2"


def test_no_mention_reads_zero_and_old_mentions_do_not_count(session, universe):
    store(session, post("Boeing is great", 25 * 60, "1"), post("Nothing here", 10, "2"), post("RT Boeing", 10, "3", is_repost=True))
    signal = post_mentions.build_post_mentions_signal(session, "BA", NOW)
    assert signal.available and signal.value == "0" and signal.would_score == 0


def test_a_post_after_the_moment_is_invisible(session, universe):
    store(session, post("Boeing is great", 10, "1"))
    earlier = post_mentions.build_post_mentions_signal(session, "BA", NOW - timedelta(minutes=20))
    # nothing was known 20 minutes ago at all
    assert not earlier.available


def test_unavailable_without_posts_a_session_or_a_matchable_symbol(session, universe):
    assert not post_mentions.build_post_mentions_signal(session, "BA", NOW).available  # nothing ever stored
    assert not post_mentions.build_post_mentions_signal(None, "BA", NOW).available
    store(session, post("Boeing is great", 10, "1"))
    assert not post_mentions.build_post_mentions_signal(session, "BTC-USD", NOW).available
    assert post_mentions.build_post_mentions_signal(session, "AAPL", NOW).value == "0"
