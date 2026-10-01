"""PR Newswire press-release collector (app/data_providers/pr_newswire.py).

What must hold: the feed is parsed tolerantly from a saved real excerpt; a
release is matched to a ticker only by an exchange tag or the company's exact
full name (never a similar name); the saved news item is known at the release's
own time (never later than the fetch); a failing feed degrades to a report
instead of an error; the fetcher is polite (retries, cache, no retry on a hard
4xx) and no test touches the network.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.data_providers import pr_newswire
from app.data_providers.base import DataProviderError
from app.data_providers.pr_newswire import (
    PressRelease,
    collect_press_releases,
    fetch_feed_text,
    match_release,
    parse_feed,
    tickers_in_text,
)
from app.knowledge import FactKind, as_of, facts_known_as_of

FIXTURE = Path(__file__).parent / "fixtures" / "pr_newswire" / "financial_feed_excerpt.xml"
FEED_XML = FIXTURE.read_text(encoding="utf-8")
FETCHED = datetime(2026, 10, 1, 21, 0)
NAMES = {"IBM": "International Business Machines Corporation", "LUV": "Southwest Airlines Co.", "AAPL": "Apple Inc."}


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _release(title="Title", summary="", url="https://www.prnewswire.com/news-releases/x-1.html"):
    return PressRelease(title=title, url=url, published_at=None, summary=summary, raw_published_at="")


# --- parsing ---------------------------------------------------------------------------


def test_parses_the_saved_feed_excerpt():
    releases = parse_feed(FEED_XML)
    assert len(releases) == 6
    southwest = next(r for r in releases if r.title.startswith("SOUTHWEST"))
    assert southwest.url.startswith("https://www.prnewswire.com/news-releases/")
    # The feed's "+0000" time becomes naive UTC.
    assert southwest.published_at == datetime(2026, 10, 1, 20, 40)
    assert "NYSE: LUV" in southwest.summary
    assert "<p>" not in southwest.summary


def test_a_feed_that_is_not_xml_or_is_empty_raises_value_error():
    with pytest.raises(ValueError):
        parse_feed("<html><body>maintenance page")
    with pytest.raises(ValueError):
        parse_feed("   ")


def test_a_feed_with_a_doctype_or_entity_is_refused():
    bomb = '<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
    with pytest.raises(ValueError):
        parse_feed(bomb)


def test_items_without_a_title_or_with_a_foreign_link_are_skipped_and_a_missing_date_is_kept_as_none():
    xml = (
        "<rss><channel>"
        "<item><title></title><link>https://www.prnewswire.com/a.html</link></item>"
        "<item><title>Off-site</title><link>https://evil.example/a.html</link></item>"
        "<item><title>Good</title><link>https://www.prnewswire.com/b.html</link><pubDate>garbage</pubDate></item>"
        "</channel></rss>"
    )
    releases = parse_feed(xml)
    assert [r.title for r in releases] == ["Good"]
    assert releases[0].published_at is None


def test_a_pub_date_without_an_offset_is_not_trusted():
    xml = "<rss><channel><item><title>T</title><link>https://www.prnewswire.com/b.html</link><pubDate>1 Oct 2026 20:40:00</pubDate></item></channel></rss>"
    assert parse_feed(xml)[0].published_at is None


# --- matching precision ----------------------------------------------------------------


def test_exchange_tags_are_read_including_a_parenthesised_list():
    assert tickers_in_text("Calamos Funds (NASDAQ: CHI, CHY, CSQ, CGO, CHW, CCD and CPZ) Announce") == {
        "CHI", "CHY", "CSQ", "CGO", "CHW", "CCD", "CPZ",
    }
    assert tickers_in_text("STAG INDUSTRIAL (NYSE:STAG) to report") == {"STAG"}
    assert tickers_in_text("shares of IBM (NYSE: IBM) rose") == {"IBM"}
    assert tickers_in_text("(NYSE American: ABC) and Nasdaq GS: XYZ") == {"ABC", "XYZ"}


def test_share_class_dots_become_dashes():
    assert tickers_in_text("Berkshire (NYSE: BRK.B)") == {"BRK-B"}


def test_a_ticker_list_stops_at_the_first_thing_that_is_not_a_ticker():
    # "the board" is prose, not a ticker: the list ends there.
    assert tickers_in_text("(NYSE: IBM, the board, MSFT)") == {"IBM"}


def test_outside_parentheses_only_the_first_ticker_is_read():
    assert tickers_in_text("NYSE: IBM, CEO said") == {"IBM"}


def test_things_that_only_look_like_tags_are_not_read():
    assert tickers_in_text("nyse: ibm") == set()  # wrong case
    assert tickers_in_text("NYSE: Apple") == set()  # a word, not a ticker
    assert tickers_in_text("The NYSE opened higher; IBM and MSFT rose") == set()  # no "EXCHANGE: TICKER"
    assert tickers_in_text("LSE: BARC") == set()  # not a US exchange we read


def test_only_watchlist_tickers_match():
    release = _release("IBM Elects Frank Baker to its Board", "ARMONK (NYSE: IBM) announced")
    assert [m.symbol for m in match_release(release, ["IBM", "AAPL"], NAMES)] == ["IBM"]
    assert match_release(release, ["AAPL"], NAMES) == []


def test_company_name_must_match_exactly_never_a_similar_name():
    exact = _release("Apple Inc. announces a new product", "")
    assert [(m.symbol, m.basis) for m in match_release(exact, ["AAPL"], NAMES)] == [("AAPL", "company_name")]
    # A short form, a different case and a longer, different name are all rejected.
    assert match_release(_release("Apple announces a new product"), ["AAPL"], NAMES) == []
    assert match_release(_release("APPLE INC. announces"), ["AAPL"], NAMES) == []
    assert match_release(_release("Pineapple Inc. announces"), ["AAPL"], NAMES) == []
    assert match_release(_release("Apple Inc.s rivals"), ["AAPL"], NAMES) == []


def test_short_names_are_never_name_matched():
    assert match_release(_release("Ford announces"), ["F"], {"F": "Ford"}) == []


def test_an_exchange_tag_wins_over_a_name_match_and_each_symbol_appears_once():
    release = _release("Apple Inc. (NASDAQ: AAPL) announces", "")
    assert [(m.symbol, m.basis) for m in match_release(release, ["AAPL"], NAMES)] == [("AAPL", "exchange_tag")]


# --- collecting ------------------------------------------------------------------------


FEEDS = ("https://feed.example/one.rss", "https://feed.example/two.rss")


def _fetch_ok(url):
    return FEED_XML


def test_collect_saves_matching_releases_as_dated_news(session):
    result = collect_press_releases(
        session, ["LUV", "IBM", "STAG", "AAPL"], fetch=_fetch_ok, names=NAMES, feed_urls=FEEDS[:1], fetched_at=FETCHED
    )
    assert result.feeds_read == 1 and result.feeds_failed == 0
    assert result.releases_seen == 6
    assert result.releases_matched == 3  # Southwest, IBM, STAG
    assert result.new == 3

    luv = facts_known_as_of(session, FactKind.NEWS, symbol="LUV")
    assert len(luv) == 1
    fact = luv[0]
    assert fact.source == "PR Newswire"
    assert fact.payload["publisher"] == "PR Newswire"
    assert fact.known_at == datetime(2026, 10, 1, 20, 40)  # the release's own time
    assert fact.known_at_basis == "source"
    assert fact.payload["url"].startswith("https://www.prnewswire.com/news-releases/")
    assert facts_known_as_of(session, FactKind.NEWS, symbol="AAPL") == []  # nothing matched: nothing saved


def test_collecting_again_saves_nothing_twice(session):
    kwargs = dict(fetch=_fetch_ok, names=NAMES, feed_urls=FEEDS[:1], fetched_at=FETCHED)
    collect_press_releases(session, ["LUV"], **kwargs)
    again = collect_press_releases(session, ["LUV"], **kwargs)
    assert again.new == 0 and again.already_saved == 1


def test_known_at_is_never_later_than_the_fetch_time(session):
    early_fetch = datetime(2026, 10, 1, 20, 0)  # before the release's stated 20:40
    collect_press_releases(session, ["LUV"], fetch=_fetch_ok, names=NAMES, feed_urls=FEEDS[:1], fetched_at=early_fetch)
    assert facts_known_as_of(session, FactKind.NEWS, symbol="LUV", include_future=True)[0].known_at == early_fetch


def test_a_release_with_no_provable_time_is_known_at_the_fetch_time(session):
    xml = (
        "<rss><channel><item><title>Acme (NYSE: ACM) names CFO</title>"
        "<link>https://www.prnewswire.com/n/1.html</link></item></channel></rss>"
    )
    collect_press_releases(session, ["ACM"], fetch=lambda u: xml, names={}, feed_urls=FEEDS[:1], fetched_at=FETCHED)
    fact = facts_known_as_of(session, FactKind.NEWS, symbol="ACM")[0]
    assert fact.known_at == FETCHED and fact.known_at_basis == "fetched"


def test_a_failing_feed_degrades_and_the_other_feed_still_runs(session):
    def fetch(url):
        if url == FEEDS[0]:
            raise DataProviderError("503")
        return FEED_XML

    result = collect_press_releases(session, ["LUV"], fetch=fetch, names=NAMES, feed_urls=FEEDS, fetched_at=FETCHED)
    assert result.feeds_failed == 1 and result.feeds_read == 1
    assert result.new == 1
    assert FEEDS[0] in result.failures[0]


def test_a_garbled_feed_is_reported_not_raised(session):
    result = collect_press_releases(
        session, ["LUV"], fetch=lambda u: "<html>blocked", names=NAMES, feed_urls=FEEDS[:1], fetched_at=FETCHED
    )
    assert result.feeds_failed == 1 and result.new == 0


def test_the_same_release_in_two_feeds_is_saved_once(session):
    result = collect_press_releases(session, ["LUV"], fetch=_fetch_ok, names=NAMES, feed_urls=FEEDS, fetched_at=FETCHED)
    assert result.new == 1 and result.already_saved == 0


def test_crypto_and_empty_symbol_lists_fetch_nothing(session):
    def boom(url):
        raise AssertionError("must not fetch")

    assert collect_press_releases(session, ["BTC-USD"], fetch=boom, feed_urls=FEEDS).skipped_reason
    assert collect_press_releases(session, [], fetch=boom, feed_urls=FEEDS).skipped_reason


def test_a_simulated_moment_neither_fetches_nor_writes(session):
    def boom(url):
        raise AssertionError("must not fetch")

    with as_of(datetime(2026, 1, 1)):
        result = collect_press_releases(session, ["LUV"], fetch=boom, names=NAMES, feed_urls=FEEDS)
    assert result.skipped_reason and result.new == 0


def test_uncommitted_session_work_is_left_alone(session):
    from app.knowledge import KnownFact

    session.add(KnownFact(kind="news", symbol="X", known_at=FETCHED, source="s", dedupe_key="k", payload={}))
    result = collect_press_releases(session, ["LUV"], fetch=_fetch_ok, names=NAMES, feed_urls=FEEDS[:1])
    assert result.skipped_reason and result.new == 0


# --- polite fetching (no network: httpx.get is replaced) -------------------------------


@pytest.fixture
def fake_http(monkeypatch):
    calls: list[str] = []
    responses: list[httpx.Response | Exception] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        assert "User-Agent" in kwargs["headers"] and kwargs["timeout"]
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(pr_newswire.httpx, "get", fake_get)
    monkeypatch.setattr(pr_newswire.time, "sleep", lambda s: None)
    return calls, responses


def _resp(status, body="<rss/>", headers=None):
    return httpx.Response(status, content=body.encode(), headers=headers, request=httpx.Request("GET", "https://x"))


def test_fetch_retries_a_5xx_then_succeeds_and_caches(fake_http):
    calls, responses = fake_http
    responses += [_resp(503), _resp(200, "<rss>ok</rss>")]
    assert fetch_feed_text("https://feed.example/a") == "<rss>ok</rss>"
    assert len(calls) == 2
    assert fetch_feed_text("https://feed.example/a") == "<rss>ok</rss>"  # served from cache
    assert len(calls) == 2


def test_fetch_retries_a_network_error(fake_http):
    calls, responses = fake_http
    responses += [httpx.ConnectTimeout("slow"), _resp(200, "<rss>ok</rss>")]
    assert fetch_feed_text("https://feed.example/b") == "<rss>ok</rss>"


def test_fetch_gives_up_after_the_attempts_with_a_clean_error(fake_http):
    calls, responses = fake_http
    responses += [_resp(503)] * pr_newswire.MAX_ATTEMPTS
    with pytest.raises(DataProviderError):
        fetch_feed_text("https://feed.example/c")
    assert len(calls) == pr_newswire.MAX_ATTEMPTS


def test_fetch_does_not_retry_a_hard_4xx(fake_http):
    calls, responses = fake_http
    responses += [_resp(403)]
    with pytest.raises(DataProviderError):
        fetch_feed_text("https://feed.example/d")
    assert len(calls) == 1


def test_fetch_refuses_an_oversized_body(fake_http, monkeypatch):
    calls, responses = fake_http
    monkeypatch.setattr(pr_newswire, "MAX_FEED_BYTES", 10)
    responses += [_resp(200, "x" * 100)]
    with pytest.raises(DataProviderError):
        fetch_feed_text("https://feed.example/e")


def test_requests_are_spaced_out(fake_http, monkeypatch):
    calls, responses = fake_http
    slept: list[float] = []
    monkeypatch.setattr(pr_newswire.time, "sleep", lambda s: slept.append(s))
    responses += [_resp(200), _resp(200)]
    fetch_feed_text("https://feed.example/f")
    fetch_feed_text("https://feed.example/g")
    assert slept and slept[0] > 0  # the second request waited for the minimum gap
