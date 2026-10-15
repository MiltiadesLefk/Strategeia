from __future__ import annotations


from app.analysis.fundamental_scoring import score_news_sentiment
from app.data_providers.base import CompanyOverview, FinancialYear, NewsItem


def _overview(**overrides) -> CompanyOverview:
    defaults = dict(
        symbol="AAPL", name="Apple", market_cap=1.0, pe_ratio=1.0, revenue_ttm=1.0, eps_ttm=1.0,
        week52_low=100.0, week52_high=200.0,
    )
    defaults.update(overrides)
    return CompanyOverview(**defaults)


def test_news_sentiment_positive_keyword_scores_up():
    news = [NewsItem(headline="Company beats earnings estimates", source="Wire", url="", published_at="")]
    score, reasons = score_news_sentiment(news)
    assert score == 1
    assert len(reasons) == 1


def test_news_sentiment_negative_keyword_scores_down():
    news = [NewsItem(headline="Company faces SEC investigation", source="Wire", url="", published_at="")]
    score, reasons = score_news_sentiment(news)
    assert score == -1


def test_news_sentiment_neutral_headline_scores_zero():
    news = [NewsItem(headline="Company announces new product event", source="Wire", url="", published_at="")]
    score, reasons = score_news_sentiment(news)
    assert score == 0
    assert reasons == []


def test_news_sentiment_caps_at_max():
    news = [
        NewsItem(headline="Stock surges to record high", source="Wire", url="", published_at=""),
        NewsItem(headline="Analysts upgrade after strong demand", source="Wire", url="", published_at=""),
        NewsItem(headline="Company announces buyback rally", source="Wire", url="", published_at=""),
    ]
    score, _ = score_news_sentiment(news)
    assert score == 2  # NEWS_SCORE_CAP


# --- direction-awareness -----------------------------------------------------
# Fundamentals and news are CONFLUENCE checks, so they have to know which way
# the trade goes. Scored direction-blind, the three strongest reasons to be
# short (collapsing revenue, 52-week low, "plunges after guidance cut") were
# counted AGAINST a short — enough to push most short setups under
# MIN_CONFIDENCE_FOR_TRADE and quietly suppress half the signal space.


def _bearish_setup():
    overview = CompanyOverview(
        symbol="ACME", name="Acme", market_cap=1e9, pe_ratio=10.0, revenue_ttm=1e9,
        eps_ttm=1.0, week52_low=50.0, week52_high=200.0,
    )
    years = [FinancialYear(2024, 1000.0, 100.0), FinancialYear(2025, 800.0, 50.0)]
    news = [NewsItem("Acme plunges after guidance cut", "x", "http://x", "")]
    return overview, years, news, 51.0  # price sitting on the 52-week low


