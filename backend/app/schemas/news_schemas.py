from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class NewsCardOut(BaseModel):
    """One AI label. Never point-in-time: see knowledge/news_cards.py."""

    event_type: str
    sentiment: str
    materiality: str
    companies_mentioned: list[str]
    one_line_summary: str
    is_about_this_company: bool
    label_model: str
    labelled_at: str


class NewsItemWithCard(BaseModel):
    headline: str
    publisher: str
    url: str
    published_at: str
    known_at: UtcDatetime
    # True for items saved from a PR Newswire press release.
    is_press_release: bool
    card: NewsCardOut | None


class NewsCardsResponse(BaseModel):
    symbol: str
    news_cards_enabled: bool
    # An AI provider is set up and usable (the Label button needs it).
    llm_configured: bool
    labelled_count: int
    unlabelled_count: int
    items: list[NewsItemWithCard]


class NewsLabelResponse(BaseModel):
    symbol: str
    labelled: int
    llm_calls: int
    skipped_batches: int
    remaining_unlabelled: int
    reason: str | None
    errors: list[str]


class NewsCollectRequest(BaseModel):
    symbols: list[str] | None = None


class NewsCollectResponse(BaseModel):
    symbols: int
    feeds_read: int
    feeds_failed: int
    releases_seen: int
    releases_matched: int
    new: int
    already_saved: int
    skipped_reason: str | None
    failures: list[str]
