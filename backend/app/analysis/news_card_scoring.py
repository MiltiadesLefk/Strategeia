"""AI-labelled news as a silent signal (a ShadowSignal reading).

What it reads: the news cards (app/knowledge/news_cards.py) for the symbol whose
headline was published in the last few days. Only cards that are about this
company and rated high or medium materiality count. Each counts with a weight
(high 2, medium 1) and a sign (positive +1, negative -1, neutral and mixed 0);
the weights add up to a net figure. A net of at least +2 (one high-materiality
good story, or two medium ones) is bullish evidence, at most -2 is bearish,
anything between is nothing.

The labels do not decide anything: this module turns them into points by fixed
rules, the AI never sees the trade, and the points are only recorded on the
plan. Direction-signed like the app's other confluence checks: bullish news
supports a long (+1) and contradicts a short (-1); with no clear direction the
signal scores 0. The existing keyword news scorer keeps deciding today.

Caveat that matters for any backtest of this signal: a card is written when the
headline is labelled, not when it was published, and the model that wrote it
may know how the story turned out. Cards are therefore only trustworthy going
forward (see news_cards.py), and a simulated past moment sees only cards that
had been written by then.

No cards in the window is reported as unavailable (never a guess), which is
different from "cards exist and none of them is material".
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session

from app.knowledge import current_as_of
from app.knowledge.news_cards import cards_for_symbol

SIGNAL_NAME = "news_cards"
# Largest swing this signal would add in either direction.
NEWS_CARD_SCORE_CAP = 1
# How recent a headline must be to count as news rather than history.
RECENT_WINDOW_DAYS = 3
MATERIALITY_WEIGHT = {"high": 2, "medium": 1}
SENTIMENT_SIGN = {"positive": 1, "negative": -1, "neutral": 0, "mixed": 0}
# Net weighted evidence needed before the signal says anything.
NET_THRESHOLD = 2


def _news_time(payload: dict) -> datetime | None:
    raw = payload.get("news_known_at")
    try:
        return datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def recent_cards(session: Session, symbol: str) -> list[dict]:
    """The labelled headlines for `symbol` published in the recent window, as known now."""
    cutoff = current_as_of() - timedelta(days=RECENT_WINDOW_DAYS)
    recent = []
    for fact in cards_for_symbol(session, symbol):
        published = _news_time(fact.payload)
        if published is not None and published >= cutoff:
            recent.append(dict(fact.payload))
    return recent


