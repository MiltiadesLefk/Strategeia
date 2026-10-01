"""AI-labelled news as a silent signal (see analysis/shadow_signals.py).

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

from app.analysis.shadow_signals import ShadowContext, ShadowSignal, shadow_signal
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


def net_news_weight(cards: list[dict]) -> tuple[int, int]:
    """(net weighted sentiment, number of cards that counted)."""
    net = counted = 0
    for card in cards:
        weight = MATERIALITY_WEIGHT.get(str(card.get("materiality")))
        if weight is None or not card.get("is_about_this_company"):
            continue
        counted += 1
        net += weight * SENTIMENT_SIGN.get(str(card.get("sentiment")), 0)
    return net, counted


def score_news_cards(direction: str | None, cards: list[dict]) -> tuple[int, str, str]:
    """(points, reason, value) for the recent cards."""
    net, counted = net_news_weight(cards)
    value = f"net {net:+d} from {counted} material item{'s' if counted != 1 else ''}"
    if counted == 0:
        return 0, f"{len(cards)} labelled headline(s) in the last {RECENT_WINDOW_DAYS} days, none material and about this company.", value
    if abs(net) < NET_THRESHOLD:
        return 0, f"Labelled news in the last {RECENT_WINDOW_DAYS} days is too light or too mixed to count ({value}).", value
    bullish = net > 0
    if direction not in ("long", "short"):
        return 0, f"Labelled news leans {'positive' if bullish else 'negative'} ({value}), but there is no clear direction to sign it by.", value
    supports = bullish == (direction == "long")
    points = NEWS_CARD_SCORE_CAP if supports else -NEWS_CARD_SCORE_CAP
    lean = "positive" if bullish else "negative"
    verb = "supports" if supports else "argues against"
    return points, f"AI-labelled news in the last {RECENT_WINDOW_DAYS} days leans {lean} ({value}), which {verb} a {direction}.", value


def build_news_card_signal(direction: str | None, session: Session | None, symbol: str) -> ShadowSignal:
    if session is None:
        return ShadowSignal(SIGNAL_NAME, None, 0, "No database session to read news cards from.", available=False)
    cutoff = current_as_of() - timedelta(days=RECENT_WINDOW_DAYS)
    recent = []
    for fact in cards_for_symbol(session, symbol):
        published = _news_time(fact.payload)
        if published is not None and published >= cutoff:
            recent.append(dict(fact.payload))
    if not recent:
        return ShadowSignal(
            SIGNAL_NAME, None, 0, f"No labelled news for this symbol in the last {RECENT_WINDOW_DAYS} days.", available=False
        )
    points, reason, value = score_news_cards(direction, recent)
    return ShadowSignal(SIGNAL_NAME, value, points, reason, available=True)


@shadow_signal(SIGNAL_NAME)
def _news_card_shadow_scorer(context: ShadowContext) -> ShadowSignal:
    return build_news_card_signal(context.direction, context.session, context.symbol)
