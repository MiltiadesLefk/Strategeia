"""Fed event window as a silent signal (see analysis/shadow_signals.py).

What it reads: whether a Fed policy statement or a Chair speech or testimony was
published within one day of the moment of the plan. Days like that move the whole
market on words, so a setup built on a calm chart is less trustworthy around them.
It works like the macro-calendar penalty that already exists, but from the Fed's
own published items instead of a hand-kept date table.

Direction-neutral and penalty-only: a Fed event says nothing about whether a
long or a short is better, only that the chart is less reliable that day. So it
reads 1 and would score -1 inside the window, and 0 outside; it never scores
positive and ignores the trade's direction. Nothing here reads what was said.

A limit worth stating plainly: the facts are read through the point-in-time
reader, which only shows what was already public. The "one day after" half of the
window is therefore fully visible, but a statement scheduled for tomorrow cannot be
seen here (it is not published yet). Upcoming FOMC dates are already covered by the
macro-calendar penalty, so this signal does not try to guess them.

Unavailable (not zero) when no Fed item has ever been stored as of the moment:
that means the facts were never loaded, which is different from "loaded, and no
event nearby".
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session

from app.analysis.shadow_signals import ShadowContext, ShadowSignal, shadow_signal
from app.data_providers.fed_feed import TYPE_POLICY_STATEMENT
from app.knowledge import FactKind, KnownFact, current_as_of, facts_known_as_of

SIGNAL_NAME = "fed_event_window"
# The caution flag's size: one point against, in either direction of trade.
FED_WINDOW_PENALTY = 1
# How close, either side of the moment, an item counts as "in the window".
WINDOW = timedelta(days=1)


def within_window(event_time: datetime, moment: datetime, window: timedelta = WINDOW) -> bool:
    """True when `event_time` is within `window` of `moment`, before or after (the
    edges count). Pure date maths on naive UTC times."""
    return abs(event_time - moment) <= window


def is_flagged_item(fact: KnownFact) -> bool:
    """A policy statement, or a speech or testimony by a Chair."""
    payload = fact.payload or {}
    if payload.get("item_type") == TYPE_POLICY_STATEMENT:
        return True
    return bool(payload.get("is_chair")) and payload.get("category") in ("speech", "testimony")


def build_fed_window_signal(session: Session | None, moment: datetime | None = None) -> ShadowSignal:
    if session is None:
        return ShadowSignal(SIGNAL_NAME, None, 0, "No database session to read Fed items from.", available=False)
    moment = moment if moment is not None else current_as_of()
    # Nothing known as of the moment at all: the stream was never loaded.
    anything = facts_known_as_of(session, FactKind.FED_SPEECH, as_of=moment, limit=1)
    if not anything:
        return ShadowSignal(SIGNAL_NAME, None, 0, "No Fed statements or speeches stored yet.", available=False)
    recent = facts_known_as_of(session, FactKind.FED_SPEECH, as_of=moment, since=moment - WINDOW)
    hits = [f for f in recent if is_flagged_item(f) and within_window(f.known_at, moment)]
    if not hits:
        return ShadowSignal(SIGNAL_NAME, "0", 0, "No Fed policy statement or Chair speech within a day.", available=True)
    newest = hits[0]
    title = (newest.payload or {}).get("title") or "a Fed item"
    return ShadowSignal(
        SIGNAL_NAME,
        "1",
        -FED_WINDOW_PENALTY,
        f"A Fed policy statement or Chair speech was published within a day ({title}): a risk flag, whichever "
        "direction the trade takes.",
        available=True,
    )


@shadow_signal(SIGNAL_NAME)
def _fed_window_shadow_scorer(context: ShadowContext) -> ShadowSignal:
    return build_fed_window_signal(context.session)
