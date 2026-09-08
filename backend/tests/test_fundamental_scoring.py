from __future__ import annotations

from datetime import date, timedelta

from app.analysis.fundamental_scoring import score_fundamentals, score_news_sentiment
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


def test_fundamentals_revenue_growth_scores_up():
    years = [FinancialYear(year=2023, revenue=100.0, net_income=10.0), FinancialYear(year=2024, revenue=120.0, net_income=12.0)]
    score, reasons = score_fundamentals(_overview(), years, None, price=150.0)
    assert score >= 1
    assert any("grew" in r for r in reasons)


def test_fundamentals_revenue_decline_scores_down():
    years = [FinancialYear(year=2023, revenue=120.0, net_income=10.0), FinancialYear(year=2024, revenue=100.0, net_income=8.0)]
    score, reasons = score_fundamentals(_overview(), years, None, price=150.0)
    assert score <= -1
    assert any("declined" in r for r in reasons)


def test_fundamentals_near_52w_high_scores_up():
    score, reasons = score_fundamentals(_overview(week52_high=200.0), [], None, price=198.0)
    assert score == 1
    assert any("52-week high" in r for r in reasons)


def test_fundamentals_near_52w_low_scores_down():
    score, reasons = score_fundamentals(_overview(week52_low=100.0, week52_high=500.0), [], None, price=101.0)
    assert score == -1
    assert any("52-week low" in r for r in reasons)


def test_fundamentals_imminent_earnings_penalizes():
    soon = date.today() + timedelta(days=1)
    score, reasons = score_fundamentals(_overview(week52_low=1.0, week52_high=1_000_000.0), [], soon, price=150.0)
    assert score == -1
    assert any("event risk" in r for r in reasons)


def test_fundamentals_distant_earnings_no_penalty():
    far = date.today() + timedelta(days=30)
    score, _ = score_fundamentals(_overview(week52_low=1.0, week52_high=1_000_000.0), [], far, price=150.0)
    assert score == 0
