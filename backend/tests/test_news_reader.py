"""The morning news reader and the big-news lane's use of AI labels (services/news_reader_service.py,
analysis/big_news.evaluate_cards). The AI is a fake; headlines are the real ones from the 2026-10 review."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.analysis.big_news import evaluate_cards
from app.config import AppSettings
from app.data_providers.base import NewsItem
from app.knowledge.news_cards import cards_for_symbol
from app.llm_providers.base import LLMResult
from app.services import news_reader_service
from app.services.news_reader_service import read_news
from app.timeutil import utcnow_naive

NOW = datetime(2026, 10, 18, 14, 0)


def _card(**o):
    base = {"is_about_this_company": True, "materiality": "high", "sentiment": "positive", "certainty": "conditional",
            "news_known_at": (NOW - timedelta(hours=3)).isoformat(), "headline": "h", "one_line_summary": "DOE offers Vistra a $4.2B loan."}
    return {**base, **o}


def test_firm_material_positive_news_is_a_hit_even_when_conditional():
    hit, block = evaluate_cards([_card()], NOW, 24)
    assert block is None and hit.category == "AI-read news" and "4.2B" in hit.headline
    assert evaluate_cards([_card(certainty="confirmed")], NOW, 24)[0] is not None


@pytest.mark.parametrize("override", [
    {"certainty": "rumor"},  # Tesla-SpaceX speculation never buys
    {"certainty": "unclear"},
    {"materiality": "medium"},
    {"is_about_this_company": False},
    {"news_known_at": (NOW - timedelta(hours=40)).isoformat()},  # outside the window
    {"sentiment": "neutral"},
])
def test_everything_else_is_no_hit(override):
    assert evaluate_cards([_card(**override)], NOW, 24) == (None, None)


def test_material_negative_news_blocks_and_the_article_replaces_the_headline():
    assert evaluate_cards([_card(sentiment="negative")], NOW, 24)[1] is not None
    assert evaluate_cards([_card(), _card(sentiment="negative")], NOW, 24)[0] is None
    # headline read positive, the article body negative: the article wins
    assert evaluate_cards([_card(article={"materiality": "high", "sentiment": "negative", "one_line_summary": "but it is cancelled"})], NOW, 24)[1]


# --- the reader --------------------------------------------------------------------------


class Provider:
    name = "fake"

    def __init__(self, news):
        self._news = news

    def get_news(self, symbol, limit=5):
        return self._news.get(symbol, [])

    def get_company_overview(self, symbol):
        raise RuntimeError("no overview")


class LLM:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def is_configured(self):
        return True

    def generate(self, prompt, *, max_tokens=300, temperature=0.4, tier="routine", response_schema=None):
        self.calls += 1
        n = sum(1 for line in prompt.splitlines() if line[:1].isdigit() and ". [" in line)
        cards = [{"index": i, "event_type": "regulatory", "sentiment": "positive", "materiality": "high", "certainty": "conditional",
                  "companies_mentioned": ["Vistra"], "one_line_summary": "DOE loan.", "is_about_this_company": True} for i in range(1, n + 1)]
        return LLMResult(json.dumps({"cards": cards}), self.name, 1, model="m")


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _news(symbol, title):
    return {symbol: [NewsItem(headline=title, source="Wire", url=f"https://x/{symbol}", published_at=utcnow_naive().strftime("%Y-%m-%dT%H:%M:%SZ"))]}


def test_reader_labels_new_headlines_once(session):
    provider, llm = Provider(_news("VST", "Vistra wins conditional $4.2 billion DOE loan")), LLM()
    read_news(AppSettings(), provider, llm, session, ["VST"])
    cards = cards_for_symbol(session, "VST")
    assert len(cards) == 1 and cards[0].payload["certainty"] == "conditional"
    read_news(AppSettings(), provider, llm, session, ["VST"])  # already labelled: no new AI call
    assert llm.calls == 1


def test_reader_does_nothing_without_ai_or_with_cards_off(session):
    provider = Provider(_news("VST", "Vistra wins $4.2 billion DOE loan"))
    assert read_news(AppSettings(), provider, None, session, ["VST"]) == {}
    llm = LLM()
    read_news(AppSettings(news_cards_enabled=False), provider, llm, session, ["VST"])
    assert llm.calls == 0 and cards_for_symbol(session, "VST") == []


def test_reader_stops_at_the_call_budget(session, monkeypatch):
    monkeypatch.setattr(news_reader_service, "MAX_LLM_CALLS_PER_SCAN", 2)
    symbols = [f"S{i}" for i in range(5)]
    news = {}
    for s in symbols:
        news.update(_news(s, f"{s} wins contract"))
    llm = LLM()
    read_news(AppSettings(), Provider(news), llm, session, symbols)
    assert llm.calls == 2


def test_market_wide_headlines_are_labelled_and_returned(session, monkeypatch):
    monkeypatch.setattr("app.services.big_news_service.wire_news_headlines",
                        lambda settings, now=None, only_big=True: {"RGTI": "Rigetti gets $100M from Commerce for an equity stake"} if not only_big else {})
    llm = LLM()
    found = read_news(AppSettings(), Provider({}), llm, session, ["VST"], market=True)
    assert list(found) == ["RGTI"] and len(cards_for_symbol(session, "RGTI")) == 1
