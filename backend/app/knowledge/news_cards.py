"""News cards: what an AI read in one headline, stored as a dated fact.

A card is the model's answer to a fixed form about ONE archived headline: what
kind of event it is, whether it reads positive or negative, how material it
sounds, which companies it names and whether it is really about this company.
The model only extracts; it never says whether to buy or sell. What a label
MEANS for a trade is decided by plain rules (analysis/news_card_scoring.py).

Idea from quant-mind (LLMQuant/quant-mind, MIT): a news record is a structured
object with an event type, tickers and a one-line summary, produced once per
item. The form, the validation and the storage here are this project's own.

IMPORTANT, and shown to the user: a card is NOT point-in-time. A model asked
about a headline from 2023 already "knows" what happened afterwards, and its
label can quietly carry that knowledge (a routine-looking 2023 headline about a
company that later collapsed can come back "negative"). So:
  * a card's `known_at` is the moment it was LABELLED, never the headline's
    publish time (the headline's own time is kept as `news_known_at` and as
    `effective_at`); a backtest simulating a past moment therefore sees no card
    that was written later, which is the honest answer;
  * every card records the model that wrote it and when (`label_model`,
    `labelled_at`), and `point_in_time` is always False;
  * cards are only meant to be tested forward in time, or on headlines
    published after the labelling model's training cutoff.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlmodel import Session

from app.knowledge import FactKind, KnownFact, facts_known_as_of, make_dedupe_key, record_fact, to_naive_utc
from app.knowledge.store import RecordedFact
from app.timeutil import utcnow_naive

CARD_SOURCE = "news_card"

EventType = Literal["earnings", "guidance", "mna", "product", "legal", "regulatory", "leadership", "analyst", "macro", "other"]
Sentiment = Literal["positive", "negative", "neutral", "mixed"]
Materiality = Literal["high", "medium", "low"]

EVENT_TYPES: tuple[str, ...] = (
    "earnings", "guidance", "mna", "product", "legal", "regulatory", "leadership", "analyst", "macro", "other",
)
SENTIMENTS: tuple[str, ...] = ("positive", "negative", "neutral", "mixed")
MATERIALITIES: tuple[str, ...] = ("high", "medium", "low")

MAX_SUMMARY_LENGTH = 200
MAX_COMPANIES = 10
MAX_COMPANY_NAME_LENGTH = 80
# The newest cards a reader looks at per symbol; far more than any window needs.
CARD_READ_LIMIT = 300


CompanyName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_COMPANY_NAME_LENGTH)]


class NewsCard(BaseModel):
    """The form the model fills in for one headline. `extra="forbid"` so a reply
    carrying fields we never asked for is rejected, not trusted."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_type: EventType = Field(description="The kind of event the headline reports.")
    sentiment: Sentiment = Field(
        description="How the headline reads for the company's shareholders: positive, negative, neutral, or mixed."
    )
    materiality: Materiality = Field(
        description="How likely the news is to matter to the stock: high (earnings, guidance, a deal, a "
        "lawsuit outcome), medium, or low (routine, promotional or tangential)."
    )
    companies_mentioned: list[CompanyName] = Field(
        max_length=MAX_COMPANIES, description="Names of the companies the headline mentions, as written."
    )
    one_line_summary: str = Field(
        min_length=1, max_length=MAX_SUMMARY_LENGTH,
        description="One plain sentence stating only what the headline says. No opinion, no advice.",
    )
    is_about_this_company: bool = Field(
        strict=True,
        description="True only when the headline is mainly about the company in question, not a sector-wide "
        "story or a passing mention."
    )


class NewsCardEntry(NewsCard):
    """One card inside a batch reply: the card plus the number of the headline it labels."""

    index: int = Field(strict=True, ge=1, description="The number of the headline this card labels, as listed.")


class NewsCardBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cards: list[NewsCardEntry]


def news_card_batch_json_schema() -> dict:
    """The JSON schema for a batch reply, with every $ref inlined (some
    structured-output modes do not follow references). A fresh dict each call."""
    schema = NewsCardBatch.model_json_schema()
    defs = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(defs[node["$ref"].split("/")[-1]])
            return {k: inline(v) for k, v in node.items()}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    return inline(schema)


def parse_card_batch(raw: str) -> NewsCardBatch | None:
    """The reply as a validated batch, or None when it is not exactly the form
    (not JSON, a wrong enum, a missing field, an extra field...). All or
    nothing: a half-valid batch is not partly trusted."""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    start = text.find("{")
    decoder = json.JSONDecoder()
    while start != -1:
        try:
            value, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        if isinstance(value, dict):
            try:
                return NewsCardBatch.model_validate(value)
            except ValueError:
                return None
        start = text.find("{", start + 1)
    return None


def card_dedupe_key(news_dedupe_key: str) -> str:
    return make_dedupe_key("card", news_dedupe_key)


def record_card(
    session: Session,
    news_fact: KnownFact,
    card: NewsCard,
    *,
    label_model: str,
    label_provider: str,
    labelled_at: datetime | None = None,
    commit: bool = True,
) -> RecordedFact:
    """Save one card for one archived headline, once. known_at is the labelling
    time (see the module note); the headline's own time stays in the payload."""
    when = to_naive_utc(labelled_at) if labelled_at is not None else utcnow_naive()
    return record_fact(
        session,
        kind=FactKind.NEWS_CARD,
        symbol=news_fact.symbol,
        source=CARD_SOURCE,
        source_ref=news_fact.source_ref,
        dedupe_key=card_dedupe_key(news_fact.dedupe_key),
        known_at=when,
        known_at_basis="fetched",
        effective_at=news_fact.known_at,
        fetched_at=when,
        commit=commit,
        payload={
            "news_dedupe_key": news_fact.dedupe_key,
            "headline": str(news_fact.payload.get("headline", "")),
            "publisher": str(news_fact.payload.get("publisher", "")),
            "url": str(news_fact.payload.get("url", "")),
            "news_known_at": news_fact.known_at.isoformat(),
            **card.model_dump(),
            "label_model": label_model,
            "label_provider": label_provider,
            "labelled_at": when.isoformat(),
            # Never True: the model may know how the story ended.
            "point_in_time": False,
        },
    )


def cards_by_news_key(session: Session, symbol: str, limit: int = CARD_READ_LIMIT) -> dict[str, KnownFact]:
    """The symbol's stored cards keyed by the dedupe key of the headline they
    label (newest card wins if there were ever two). Reads go through the
    look-ahead guard, so inside a simulated moment only cards written by then show."""
    out: dict[str, KnownFact] = {}
    for fact in facts_known_as_of(session, FactKind.NEWS_CARD, symbol=symbol, limit=limit):
        key = str(fact.payload.get("news_dedupe_key", ""))
        if key and key not in out:
            out[key] = fact
    return out


def cards_for_symbol(session: Session, symbol: str, limit: int = CARD_READ_LIMIT) -> list[KnownFact]:
    return list(cards_by_news_key(session, symbol, limit).values())

