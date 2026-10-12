"""Company headlines (app/services/company_news.py, app/data_providers/google_news.py): the Google search is
by company, a top-up from the data providers keeps only headlines naming the company, a failing source never
raises while another answered, and nothing is invented."""

from __future__ import annotations

import pytest

from app.data_providers.base import AllProvidersFailedError, DataProviderError, NewsItem
from app.data_providers.google_news import parse_feed
from app.knowledge import as_of
from app.services import company_news
from app.services.company_news import build_query, fetch_company_news
from app.timeutil import utcnow_naive

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>Vistra stock jumps on loan hopes - Yahoo Finance</title><link>https://n.example/1</link>
<pubDate>Mon, 12 Oct 2026 08:00:00 GMT</pubDate><source url="https://finance.yahoo.com">Yahoo Finance</source></item>
<item><title>DOE intends to loan Vistra $4.2B - Utility Dive</title><link>https://n.example/2</link>
<pubDate>Tue, 13 Oct 2026 09:30:00 GMT</pubDate><source url="https://utilitydive.com">Utility Dive</source></item>
</channel></rss>"""


def item(headline, source="Wire"):
    return NewsItem(headline=headline, source=source, url=f"https://x.example/{abs(hash(headline))}", published_at="")


class FakeData:
    def __init__(self, items=None, error=None):
        self.items, self.error, self.calls = items or [], error, 0

    def get_news(self, symbol, limit=5):
        self.calls += 1
        if self.error:
            raise self.error
        return self.items[:limit]


@pytest.fixture(autouse=True)
def google(monkeypatch):
    state = {"items": [], "error": None, "queries": []}

    class FakeGoogle:
        def search(self, query, limit=5, days=7):
            state["queries"].append(query)
            if state["error"]:
                raise state["error"]
            return state["items"][:limit]

    monkeypatch.setattr(company_news, "_google", FakeGoogle())
    monkeypatch.setenv("GOOGLE_NEWS_ENABLED", "true")
    return state


def test_feed_is_parsed_newest_first_with_publisher_stripped_from_the_title():
    items = parse_feed(FEED)
    assert [i.headline for i in items] == ["DOE intends to loan Vistra $4.2B", "Vistra stock jumps on loan hopes"]
    assert items[0].source == "Utility Dive" and items[0].published_at.endswith("Z")


def test_feed_with_entities_or_garbage_is_refused():
    with pytest.raises(DataProviderError):
        parse_feed(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss><channel/></rss>')
    with pytest.raises(DataProviderError):
        parse_feed(b"not xml")


def test_query_uses_the_company_name_and_both_exchange_tickers():
    assert build_query("VST", "Vistra Corp.") == '"vistra" OR "NYSE:VST" OR "NASDAQ:VST"'
    assert build_query("BTC-USD", None) is None


def test_google_headlines_come_first_and_fill_the_list(google):
    google["items"] = [item(f"Vistra story {i}") for i in range(5)]
    data = FakeData([item("Peer story")])
    out = fetch_company_news("VST", "Vistra Corp.", data)
    assert len(out) == 5 and data.calls == 0


def test_top_up_keeps_only_headlines_that_name_the_company_and_skips_duplicates(google):
    google["items"] = [item("Vistra stock jumps")]
    data = FakeData([item("Vistra stock jumps"), item("Constellation vs Talen"), item("Is Vistra a buy today?")])
    out = fetch_company_news("VST", "Vistra Corp.", data)
    assert [i.headline for i in out] == ["Vistra stock jumps", "Is Vistra a buy today?"]


def test_a_failing_google_falls_back_to_the_data_providers(google):
    google["error"] = DataProviderError("down")
    out = fetch_company_news("VST", "Vistra Corp.", FakeData([item("Vistra raises guidance")]))
    assert [i.headline for i in out] == ["Vistra raises guidance"]


def test_nothing_is_invented_when_every_source_fails(google):
    google["error"] = DataProviderError("down")
    with pytest.raises(AllProvidersFailedError):
        fetch_company_news("VST", "Vistra Corp.", FakeData(error=AllProvidersFailedError("none")))


def test_google_result_survives_a_failing_top_up(google):
    google["items"] = [item("Vistra story")]
    out = fetch_company_news("VST", "Vistra Corp.", FakeData(error=AllProvidersFailedError("none")))
    assert len(out) == 1


def test_a_simulated_moment_never_uses_google(google):
    data = FakeData([item("Old headline")])
    with as_of(utcnow_naive()):
        out = fetch_company_news("VST", "Vistra Corp.", data)
    assert google["queries"] == [] and [i.headline for i in out] == ["Old headline"]


def test_switch_off_uses_only_the_data_providers(google, monkeypatch):
    monkeypatch.setenv("GOOGLE_NEWS_ENABLED", "false")
    out = fetch_company_news("VST", "Vistra Corp.", FakeData([item("Peer story")]))
    assert google["queries"] == [] and len(out) == 1


def test_duplicate_google_headlines_are_listed_once(google):
    google["items"] = [item("Vistra options chain"), item("Vistra options chain"), item("Vistra raises guidance")]
    out = fetch_company_news("VST", "Vistra Corp.", FakeData())
    assert [i.headline for i in out] == ["Vistra options chain", "Vistra raises guidance"]
