from __future__ import annotations


from app.data_providers.base import NewsItem

# Deliberately simple keyword matching, not sentiment ML/an LLM call — same
# "non-ML, rule-based, named thresholds" spirit as analysis/trend.py and
# scanner_scoring.py (see CLAUDE.md). Every headline that trips a keyword is
# recorded in `reasons` so the score is always traceable to real text, never
# a black box.
POSITIVE_NEWS_KEYWORDS = (
    "beat", "beats", "beating", "surge", "surges", "surged", "upgrade", "upgraded", "upgrades",
    "record", "raises guidance", "raises forecast", "rally", "rallies", "outperform", "buyback",
    "expansion", "partnership", "strong demand", "all-time high",
)
NEGATIVE_NEWS_KEYWORDS = (
    "miss", "misses", "missed", "plunge", "plunges", "plunged", "downgrade", "downgraded",
    "downgrades", "lawsuit", "investigation", "probe", "recall", "layoff", "layoffs", "bankruptcy",
    "fraud", "cuts guidance", "cuts forecast", "warns", "warning", "sec charges", "delisted",
)
NEWS_SCORE_CAP = 2

NEAR_52W_HIGH_PCT = 0.05
NEAR_52W_LOW_PCT = 0.05
EARNINGS_IMMINENT_DAYS = 3

# score_news_sentiment's max is +/-NEWS_SCORE_CAP; score_fundamentals below
# can swing roughly +/-3 (revenue trend, 52w proximity, earnings-imminent
# penalty) — trade_plan_service combines both with the 0-6 technical score.
FUNDAMENTAL_SCORE_CAP = 3


def _direction_sign(direction: str | None) -> int:
    """+1 for a long, -1 for a short, +1 when there's no direction yet.

    Bullish evidence CONFIRMS a long and CONTRADICTS a short, and vice versa.
    Scoring it the same way for both used to penalise shorts with their own
    supporting evidence: a name with collapsing revenue, sitting on its
    52-week low, with "plunges after guidance cut" headlines scored -3 as a
    short candidate — the three strongest reasons to be short, counted
    against being short. `score_market_confirmation` and
    `score_options_positioning` already take a direction; these now match."""
    return -1 if direction == "short" else 1


def score_news_sentiment(news: list[NewsItem], direction: str | None = None) -> tuple[int, list[str]]:
    """Headline sentiment, signed for `direction` — see _direction_sign.
    Reasons stay phrased in absolute terms ("negative headline: ...") because
    that's what the text literally is; whether it helps or hurts is the
    score's job, not the label's."""
    sign = _direction_sign(direction)
    score = 0
    reasons: list[str] = []
    for item in news:
        headline = item.headline.lower()
        if any(kw in headline for kw in POSITIVE_NEWS_KEYWORDS):
            score += sign
            reasons.append(f"positive headline: “{item.headline}”")
        elif any(kw in headline for kw in NEGATIVE_NEWS_KEYWORDS):
            score -= sign
            reasons.append(f"negative headline: “{item.headline}”")
    return max(-NEWS_SCORE_CAP, min(NEWS_SCORE_CAP, score)), reasons

