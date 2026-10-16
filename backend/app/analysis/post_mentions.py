"""Posts that name the company, as a silent signal (see analysis/shadow_signals.py).

What it reads: how many posts by Donald Trump (read from an unofficial archive
feed) named this company in the 24 hours before the moment of the plan, by the same
strict name-and-ticker rules the posts watcher uses (analysis/post_topics.py).
Reposts and posts with no words are not counted.

Direction-neutral and penalty-only: a post can be good or bad for a company and
nothing here reads which, so the direction of the trade is ignored. A mention
reads as its count and would score -1 (a caution flag: the stock may be about to
move on news the chart does not show); no mention reads 0 and scores 0. It never
scores positive.

Unavailable (not zero) when no post has ever been stored as of the moment: that
means the feed was never loaded (the archive only began being recorded when the
watcher was first switched on), which is different from "loaded, and not
mentioned". Testing this honestly needs hourly price bars, because a post moves a
price in minutes, and only about two years of those exist.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session

from app.analysis.post_topics import SymbolMatcher, build_matcher, match_symbols
from app.analysis.shadow_signals import ShadowContext, ShadowSignal, shadow_signal
from app.knowledge import FactKind, current_as_of, facts_known_as_of

SIGNAL_NAME = "post_mentions"
# The caution flag's size: one point against, in either direction of trade.
POST_MENTION_PENALTY = 1
# How far back a post still counts as news.
WINDOW = timedelta(hours=24)


def _matcher_for(symbol: str) -> SymbolMatcher | None:
    from app.data_providers.universe import load_universe

    name = next((e.name for e in load_universe() if e.symbol == symbol.upper()), symbol)
    return build_matcher(symbol, name)


def build_post_mentions_signal(session: Session | None, symbol: str, moment: datetime | None = None) -> ShadowSignal:
    if session is None:
        return ShadowSignal(SIGNAL_NAME, None, 0, "No database session to read posts from.", available=False)
    moment = moment if moment is not None else current_as_of()
    if not facts_known_as_of(session, FactKind.POST, as_of=moment, limit=1):
        return ShadowSignal(SIGNAL_NAME, None, 0, "No posts stored yet.", available=False)
    matcher = _matcher_for(symbol)
    if matcher is None:
        return ShadowSignal(SIGNAL_NAME, None, 0, "This symbol cannot be matched to a name safely.", available=False)
    recent = facts_known_as_of(session, FactKind.POST, as_of=moment, since=moment - WINDOW)
    count = sum(
        1
        for fact in recent
        if not (fact.payload or {}).get("is_repost")
        and match_symbols((fact.payload or {}).get("text") or "", (matcher,))
    )
    if count == 0:
        return ShadowSignal(SIGNAL_NAME, "0", 0, "No post named this company in the last 24 hours.", available=True)
    return ShadowSignal(
        SIGNAL_NAME,
        str(count),
        -POST_MENTION_PENALTY,
        f"{count} post(s) in the last 24 hours named this company: a caution flag (the direction is not read), "
        "from an unofficial archive.",
        available=True,
    )


@shadow_signal(SIGNAL_NAME)
def _post_mentions_shadow_scorer(context: ShadowContext) -> ShadowSignal:
    return build_post_mentions_signal(context.session, context.symbol)
