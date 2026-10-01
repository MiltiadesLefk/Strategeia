"""Federal Reserve statements, speeches and testimony as dated facts.

Source: the Fed's own public RSS feeds (no key, no sign-up):

  press_monetary.xml   monetary policy press releases (the FOMC statement, the
                       economic projections, the minutes of meetings)
  speeches.xml         speeches by Board members
  testimony.xml        testimony to Congress

Each feed lists only its newest ~15 items, which reaches back months for the
policy feed and roughly a month or two for speeches. Older history lives in the
Fed's yearly archive pages; those are not read here (see `backfill_fed`).

What is stored: one `fed_speech` fact per item, market-wide (no symbol), with

  known_at     = the item's publication time from the feed (RFC 822 with a time
                 zone, converted to UTC; basis "source"). An item whose time
                 carries no zone is skipped rather than guessed.
  effective_at = the date of the event (the publication day)
  payload      = title, link, feed category, a rule-based `item_type`, the speaker
                 for speeches and testimony, whether that speaker is a Chair, and
                 the feed's one-line description.

Speakers: speech and testimony titles start with the speaker's surname
("Waller, Payments in the Age of AI Agents"). `CHAIR_SURNAMES` and
`GOVERNOR_SURNAMES` are small hand-kept lists of who counts as a Chair or a
Board member for the watcher; update them when the Board changes. Anyone else
(staff, reserve bank presidents' regulatory pieces) is stored but never alerts.

The tone of a speech (hawkish or dovish) is deliberately NOT read here. Judging
tone needs a language model, and a model's reading of a speech can only be tested
honestly on speeches made after the model's training cut-off. When that is built it
should be a separate fact kind written next to these facts (keyed by the item's
link) and must stay a recorded, unscored signal until such a test exists. Nothing
in this module or the Fed watcher calls an AI.
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from sqlmodel import Session

from app.data_providers.base import DataProviderError
from app.data_providers.feed_http import fetch_feed
from app.knowledge import FactKind, RecordedFact, make_dedupe_key, record_fact

logger = logging.getLogger(__name__)

SOURCE_NAME = "federalreserve.gov"
DEDUPE_PREFIX = "fed"

FEED_BASE = "https://www.federalreserve.gov/feeds/"
# feed file -> the category its items get when the item itself names none we know.
FEEDS: dict[str, str] = {
    "press_monetary.xml": "monetary_policy",
    "speeches.xml": "speech",
    "testimony.xml": "testimony",
}
CATEGORIES = frozenset(FEEDS.values())
_ITEM_CATEGORY = {"monetary policy": "monetary_policy", "speech": "speech", "testimony": "testimony"}

# Item types (rule-based, from the title). "policy_statement" is the FOMC decision
# itself; the others are the follow-up documents of a meeting.
TYPE_POLICY_STATEMENT = "policy_statement"
TYPE_PROJECTIONS = "projections"
TYPE_FOMC_MINUTES = "fomc_minutes"
TYPE_OTHER_MONETARY = "other_monetary"
TYPE_SPEECH = "speech"
TYPE_TESTIMONY = "testimony"

# People whose words move markets. Surnames as they open a speech title.
# The Chair gets a stronger flag than a Governor. Powell is listed with the
# current Chair: "Chair" here means "whoever has chaired within the feed's own
# history", so a speech by a former Chair now serving as a governor is flagged as
# a Chair speech too. That errs towards more caution, which is the direction a
# risk flag should err in.
CHAIR_SURNAMES = frozenset({"powell", "warsh"})
GOVERNOR_SURNAMES = frozenset({"jefferson", "bowman", "waller", "cook", "barr", "miran", "kugler"})

_FORBIDDEN_XML_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_STATEMENT_RE = re.compile(r"\bFOMC statement\b", re.IGNORECASE)
_PROJECTIONS_RE = re.compile(r"\beconomic projections\b", re.IGNORECASE)
_FOMC_MINUTES_RE = re.compile(r"^Minutes of the Federal Open Market Committee", re.IGNORECASE)
_SPEAKER_RE = re.compile(r"^(?P<speaker>[A-Z][A-Za-z'’\-]+),\s+\S")


@dataclass(frozen=True)
class FedItem:
    guid: str
    title: str
    link: str
    published: datetime  # naive UTC
    category: str  # one of CATEGORIES
    description: str = ""
    speaker: str | None = None  # surname, for speeches and testimony

    @property
    def item_type(self) -> str:
        return classify_item(self.category, self.title)

    @property
    def is_chair(self) -> bool:
        return (self.speaker or "").lower() in CHAIR_SURNAMES

    @property
    def is_governor(self) -> bool:
        return (self.speaker or "").lower() in GOVERNOR_SURNAMES


def classify_item(category: str, title: str) -> str:
    """The item's type from its feed category and title (rules only)."""
    if category == "speech":
        return TYPE_SPEECH
    if category == "testimony":
        return TYPE_TESTIMONY
    if _STATEMENT_RE.search(title):
        return TYPE_POLICY_STATEMENT
    if _PROJECTIONS_RE.search(title):
        return TYPE_PROJECTIONS
    if _FOMC_MINUTES_RE.search(title):
        return TYPE_FOMC_MINUTES
    return TYPE_OTHER_MONETARY


def speaker_from_title(title: str) -> str | None:
    """"Waller, Payments in ..." -> "Waller"; None when the title does not open
    with a surname and a comma."""
    match = _SPEAKER_RE.match(title.strip())
    return match["speaker"] if match else None


def _clean_text(raw: str | None) -> str:
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", raw or ""))).strip()


def parse_pub_date(raw: str | None) -> datetime | None:
    """RFC 822 date with a zone ("Wed, 16 Sep 2026 18:00:00 GMT") -> naive UTC.
    None for a missing, unreadable or zone-less value: a time we cannot place on
    the UTC clock is not stored as a guess."""
    if not raw or not raw.strip():
        return None
    try:
        parsed = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def parse_fed_feed(xml: bytes, default_category: str) -> list[FedItem]:
    """Items of one Fed RSS feed. Items that do not parse (no title, link or
    usable time) are skipped; a body that is not XML raises DataProviderError."""
    if default_category not in CATEGORIES:
        raise ValueError(f"unknown Fed category {default_category!r}")
    if _FORBIDDEN_XML_RE.search(xml):
        raise DataProviderError("fed feed contained a DOCTYPE or ENTITY declaration; refused")
    try:
        # The feeds start with a byte-order mark; decode it away before parsing.
        root = ET.fromstring(xml.decode("utf-8-sig").encode("utf-8"))
    except (ET.ParseError, UnicodeDecodeError) as exc:
        raise DataProviderError(f"fed feed was not valid XML: {exc}") from exc
    items: list[FedItem] = []
    for node in root.iter("item"):
        title = _clean_text(node.findtext("title"))
        link = (node.findtext("link") or "").strip()
        guid = (node.findtext("guid") or "").strip() or link
        published = parse_pub_date(node.findtext("pubDate"))
        if not title or not link or published is None:
            logger.warning("fed feed: skipped an item without a title, link or usable time (%r)", title[:60])
            continue
        category = _ITEM_CATEGORY.get((node.findtext("category") or "").strip().lower(), default_category)
        speaker = speaker_from_title(title) if category in ("speech", "testimony") else None
        items.append(
            FedItem(
                guid=guid,
                title=title,
                link=link,
                published=published,
                category=category,
                description=_clean_text(node.findtext("description")),
                speaker=speaker,
            )
        )
    return items


def ingest_fed_item(session: Session, item: FedItem, *, commit: bool = True) -> RecordedFact:
    """Store one item as a dated fact. Safe to repeat: the item's guid is the key."""
    return record_fact(
        session,
        kind=FactKind.FED_SPEECH,
        source=SOURCE_NAME,
        symbol=None,
        source_ref=item.link,
        dedupe_key=make_dedupe_key(DEDUPE_PREFIX, item.guid),
        known_at=item.published,
        effective_at=datetime(item.published.year, item.published.month, item.published.day),
        payload={
            "title": item.title,
            "link": item.link,
            "category": item.category,
            "item_type": item.item_type,
            "speaker": item.speaker,
            "is_chair": item.is_chair,
            "is_governor": item.is_governor,
            "description": item.description,
        },
        commit=commit,
    )


FetchFeed = Callable[[str], bytes]


@dataclass
class FedFetchResult:
    items: list[FedItem] = field(default_factory=list)
    feeds_ok: int = 0
    errors: list[str] = field(default_factory=list)


def fetch_fed_items(fetch: FetchFeed = fetch_feed) -> FedFetchResult:
    """Download and parse every Fed feed. A feed that fails is reported in
    `errors` and the others still count; the caller decides what a partial result
    means. Items are returned oldest first."""
    result = FedFetchResult()
    for filename, category in FEEDS.items():
        try:
            result.items.extend(parse_fed_feed(fetch(FEED_BASE + filename), category))
            result.feeds_ok += 1
        except DataProviderError as exc:
            result.errors.append(f"{filename}: {exc}")
    result.items.sort(key=lambda i: (i.published, i.guid))
    return result


@dataclass
class FedBackfillReport:
    feeds_ok: int = 0
    items_seen: int = 0
    created: int = 0
    already_stored: int = 0
    errors: list[str] = field(default_factory=list)


def backfill_fed(session: Session, fetch: FetchFeed = fetch_feed) -> FedBackfillReport:
    """Store everything the feeds currently list. A feed holds only its newest
    ~15 items, so this reaches back a few months at most; it is the way to seed
    the table after installing, and re-running it is harmless (items already
    stored are counted, not stored again, and a feed that failed last time simply
    gets another try). History older than the feeds reach is not loaded."""
    fetched = fetch_fed_items(fetch)
    report = FedBackfillReport(feeds_ok=fetched.feeds_ok, items_seen=len(fetched.items), errors=list(fetched.errors))
    for item in fetched.items:
        if ingest_fed_item(session, item).created:
            report.created += 1
        else:
            report.already_stored += 1
    return report
