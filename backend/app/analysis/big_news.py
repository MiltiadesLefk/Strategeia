"""Big-news buy lane, pure rules (no AI, no I/O): which headlines count as "big" and which block a buy.

Fixed, named pattern lists matched against a headline (case-insensitive, on word edges). Changing a pattern
changes what is bought, so the lists are in the strategy fingerprint; bump RULES_REVISION when logic changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Big, positive, about the company itself: (category, regexes).
BIG_NEWS_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("FDA approval", (r"fda.{0,40}(approv\w*|clear(s|ed|ance))", r"(approv\w*|clear(s|ed|ance)).{0,40}fda")),
    ("contract award", (r"(awarded|wins?|won|secures?|lands?).{0,40}contract", r"contract award")),
    ("takeover offer", (
        r"(to be|agrees? to be|being) (acquired|bought|taken private)", r"(takeover|buyout|acquisition) (bid|offer|proposal)",
        r"merger agreement", r"definitive agreement to be acquired",
    )),
    ("guidance raise / earnings beat", (
        r"(raises?|raised|lifts?|boosts?|hikes?).{0,30}(guidance|outlook|forecast)",
        r"(beats?|tops?|crush(es)?|smash(es)?|surpass(es)?).{0,30}(earnings|estimates|expectations|eps)",
    )),
    ("large buyback", (
        r"(share|stock)\s+(buyback|repurchase).{0,40}\$?\d+(\.\d+)?\s?(billion|bn)",
        r"\$?\d+(\.\d+)?\s?(billion|bn).{0,40}(buyback|repurchase)",
    )),
)
# Item 1.01 (a material definitive agreement) in an 8-K counts as big too.
BIG_8K_ITEMS: tuple[str, ...] = ("1.01",)

# The noisiest category: switch it off here.
INCLUDE_ANALYST_UPGRADES = True
ANALYST_UPGRADE_PATTERNS: tuple[str, ...] = (r"upgrade[sd]?", r"raises?.{0,20}price target")

# Hard blocks: any of these in the lookback window stops the buy.
NEGATIVE_PATTERNS: tuple[str, ...] = (
    r"bankruptcy", r"chapter 11", r"delist(ing|ed|s)?", r"restate(s|d|ment|ments)?", r"going[- ]concern",
    r"sec (investigation|subpoena)", r"fraud", r"downgrade[sd]?", r"(cuts?|lowers?|slashes?).{0,30}(guidance|outlook|forecast)",
)


@dataclass(frozen=True)
class BigNewsHit:
    category: str
    headline: str


def _any(patterns: tuple[str, ...], text: str) -> bool:
    # Each pattern must start and end on a word edge, so "fda" never matches inside another word.
    return any(re.search(r"(?<!\w)(?:" + p + r")(?!\w)", text, re.IGNORECASE) for p in patterns)


def classify_headline(headline: str) -> str | None:
    """The big-news category of a headline, or None."""
    for category, patterns in BIG_NEWS_PATTERNS:
        if _any(patterns, headline):
            return category
    if INCLUDE_ANALYST_UPGRADES and _any(ANALYST_UPGRADE_PATTERNS, headline):
        return "analyst upgrade"
    return None


def is_negative(headline: str) -> bool:
    return _any(NEGATIVE_PATTERNS, headline)


def parse_published(value: str) -> datetime | None:
    """Naive UTC from an ISO string; None when it cannot be read (an undated item is never "recent")."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return parsed if parsed.tzinfo is None else parsed.astimezone(timezone.utc).replace(tzinfo=None)


def evaluate_headlines(items: list[tuple[str, datetime | None]], now: datetime, lookback_hours: float) -> tuple[BigNewsHit | None, str | None]:
    """(first big hit, negative headline that blocks it). Only dated items inside the window count as big; an
    undated headline can still block (a missing date is never a reason to buy)."""
    since = now - timedelta(hours=lookback_hours)
    hit: BigNewsHit | None = None
    for headline, published in items:
        if is_negative(headline) and (published is None or published >= since):
            return None, headline
        if hit is None and published is not None and since <= published <= now and (category := classify_headline(headline)):
            hit = BigNewsHit(category, headline)
    return hit, None
