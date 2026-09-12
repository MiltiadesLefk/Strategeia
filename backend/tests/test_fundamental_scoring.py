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


def test_bearish_evidence_supports_a_short_instead_of_penalising_it():
    overview, years, news, price = _bearish_setup()

    short_f, _ = score_fundamentals(overview, years, None, price, "short")
    short_n, _ = score_news_sentiment(news, "short")
    long_f, _ = score_fundamentals(overview, years, None, price, "long")
    long_n, _ = score_news_sentiment(news, "long")

    assert short_f > 0 and short_n > 0, "bearish evidence must confirm a short"
    assert long_f < 0 and long_n < 0, "the same evidence must contradict a long"
    assert short_f == -long_f and short_n == -long_n


def test_bullish_evidence_still_supports_a_long():
    overview = CompanyOverview(
        symbol="ACME", name="Acme", market_cap=1e9, pe_ratio=10.0, revenue_ttm=1e9,
        eps_ttm=1.0, week52_low=50.0, week52_high=200.0,
    )
    years = [FinancialYear(2024, 800.0, 50.0), FinancialYear(2025, 1000.0, 100.0)]
    news = [NewsItem("Acme beats and raises guidance", "x", "http://x", "")]

    assert score_fundamentals(overview, years, None, 199.0, "long")[0] > 0
    assert score_news_sentiment(news, "long")[0] > 0


def test_no_direction_keeps_the_original_long_biased_reading():
    """Back-compat: callers that don't pass a direction (and the scanner's own
    pre-direction pass) behave exactly as before."""
    overview, years, news, price = _bearish_setup()
    assert score_fundamentals(overview, years, None, price) == score_fundamentals(overview, years, None, price, "long")
    assert score_news_sentiment(news) == score_news_sentiment(news, "long")


def test_earnings_risk_stays_a_penalty_in_both_directions():
    """The one genuinely direction-neutral factor: an earnings print can gap
    through a stop either way, so it must not flip sign."""
    overview = CompanyOverview(
        symbol="ACME", name="Acme", market_cap=1e9, pe_ratio=10.0, revenue_ttm=1e9,
        eps_ttm=1.0, week52_low=50.0, week52_high=200.0,
    )
    soon = date.today() + timedelta(days=1)
    long_score, long_reasons = score_fundamentals(overview, [], soon, 120.0, "long")
    short_score, short_reasons = score_fundamentals(overview, [], soon, 120.0, "short")

    assert long_score == short_score == -1
    assert any("earnings" in r for r in long_reasons)
    assert any("earnings" in r for r in short_reasons)
