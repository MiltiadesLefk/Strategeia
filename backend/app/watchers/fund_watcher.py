"""The fund filings watcher: new 13F reports from followed funds and new 5% owner
filings (Schedule 13D / 13G) for the watchlist.

Every poll finds filings the app has not stored yet, stores them as dated facts
(13F rows through `sec_13f`, 13D/13G through `sec_13dg`, each with its SEC
acceptance time as `known_at`), and only then returns events. The runner records
the events after that, so the facts exist whatever happens to an alert.

What becomes an event:

  * a followed fund's NEW 13F report: one market-wide summary event (how many
    positions are new, added, trimmed and sold out, and the biggest moves). It has
    no symbol, so it is only ever an alert and never starts an evaluation;
  * a NEW position of a followed fund in a watchlist symbol: one event per symbol
    (largest first, at most MAX_NEW_POSITION_EVENTS per filing). This is the one
    13F case that can start an ordinary evaluation of the symbol, because a fund
    newly owning a stock the app watches is worth a fresh look, and the
    evaluation applies every normal rule;
  * a Schedule 13D on a watchlist company (an amendment is information only), and
    a NEW Schedule 13G (a passive holder crossing 5%). Amended 13Gs are stored and
    stay quiet: index managers re-file them for nearly every large company every
    year.

A 13F is long positions only, a snapshot of a quarter's last day, up to 45 days
late; a 13D/13G is dated by the day SEC accepted it. The alert text says "as of"
the quarter end for exactly that reason.

Two ways to look for ownership filings, by watchlist size (as the SEC filings
watcher does): up to FEED_MODE_MIN_SYMBOLS companies, each company's own
submissions index; more than that, SEC's market-wide "latest filings" feeds are
read first and only the companies found there are asked about.

Failures: a request SEC refuses or rate-limits ends the poll. With nothing found
so far it raises (the runner records the error and backs off); a poll that
already has events returns them.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.data_providers.base import DataProviderError
from app.data_providers.sec_13dg import (
    FORM_13D,
    FORM_13D_A,
    FORM_13G,
    fetch_ownership_filings,
    ticker_for_cik,
)
from app.data_providers.sec_13f import (
    IssuerMatcher,
    default_matcher,
    fetch_new_13f,
    followed_fund_ciks,
    starter_fund_name,
)
from app.data_providers.sec_8k import index_url
from app.data_providers.sec_client import SecClient, get_sec_client
from app.data_providers.sec_form4 import resolve_cik
from app.knowledge.fund_holdings import (
    STATUS_ADDED,
    STATUS_NEW,
    STATUS_SOLD_OUT,
    STATUS_TRIMMED,
    FundChange,
    fund_changes_as_of,
)
from app.watchers.base import Watcher, WatcherContext, WatcherEvent
from app.watchers.registry import get_watcher, register_watcher

logger = logging.getLogger(__name__)

WATCHER_NAME = "fund_filings"

# Funds file four times a year and 5% owners now and then: every six hours is
# frequent enough to see a filing the day it appears and gentle on SEC.
POLL_INTERVAL_SECONDS = 6 * 60 * 60
# No cooldown: several followed funds file on the same deadline day and each
# deserves its own alert. The daily cap is the brake.
COOLDOWN_SECONDS = 0
DAILY_FIRE_CAP = 30

# A 13F accepted longer ago than this is stored but never alerts (it is no longer
# news; it also keeps a first run on an empty database from announcing a quarter
# of old filings). Ownership filings are news for a shorter time.
FUND_EVENT_MAX_AGE = timedelta(days=10)
OWNERSHIP_EVENT_MAX_AGE = timedelta(hours=72)

# Ownership filings are looked for among those filed in the last two weeks.
OWNERSHIP_LOOKBACK_DAYS = 14
MAX_NEW_POSITION_EVENTS = 10
# How many of the biggest moves the summary names.
SUMMARY_TOP_MOVES = 3

FEED_MODE_MIN_SYMBOLS = 40
FEED_PAGE_SIZE = 100
FEED_MAX_PAGES = 4
FEED_OVERLAP = timedelta(hours=12)
FEED_FIRST_LOOKBACK = timedelta(days=3)
FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&company=&dateb=&owner=include"
    "&start={start}&count={count}&output=atom"
)
FEED_FORM_TYPES = ("SCHEDULE%2013D", "SCHEDULE%2013G")

MAX_CONSECUTIVE_ERRORS = 3

_FEED_TITLE_RE = re.compile(r"^(?P<form>SCHEDULE 13[DG](?:/A)?) - .*\((?P<cik>\d{4,10})\) \((?P<role>[A-Za-z ]+)\)\s*$")
_FORBIDDEN_XML_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_ATOM = "{http://www.w3.org/2005/Atom}"


# --------------------------------------------------------------------------
# The ownership feed
# --------------------------------------------------------------------------


def parse_ownership_feed(xml: bytes) -> list[dict]:
    """Entries of one Atom page -> [{form, cik, role, accession, updated}] with
    `updated` in naive UTC. A filing is listed twice, once for the filer and once
    for the company it is about ("Subject"). Entries that do not parse are skipped."""
    if _FORBIDDEN_XML_RE.search(xml):
        raise DataProviderError("sec feed contained a DOCTYPE or ENTITY declaration; refused")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise DataProviderError(f"sec feed was not valid XML: {exc}") from exc
    out: list[dict] = []
    for entry in root.findall(f"{_ATOM}entry"):
        title = (entry.findtext(f"{_ATOM}title") or "").strip()
        match = _FEED_TITLE_RE.match(title)
        entry_id = entry.findtext(f"{_ATOM}id") or ""
        accession = entry_id.split("accession-number=")[-1] if "accession-number=" in entry_id else ""
        if not match or not accession:
            continue
        try:
            updated = datetime.fromisoformat(entry.findtext(f"{_ATOM}updated") or "")
        except ValueError:
            continue
        if updated.tzinfo is not None:
            updated = updated.astimezone(timezone.utc).replace(tzinfo=None)
        out.append(
            {
                "form": match["form"],
                "cik": int(match["cik"]),
                "role": match["role"].strip().lower(),
                "accession": accession,
                "updated": updated,
            }
        )
    return out


def _feed_subject_ciks(client: SecClient, since: datetime, wanted: set[int]) -> set[int]:
    """CIKs on `wanted` that are the subject of a 13D/13G newer than `since`."""
    found: set[int] = set()
    for form_type in FEED_FORM_TYPES:
        for page in range(FEED_MAX_PAGES):
            entries = parse_ownership_feed(
                client.get_bytes(FEED_URL.format(form=form_type, start=page * FEED_PAGE_SIZE, count=FEED_PAGE_SIZE))
            )
            for entry in entries:
                if entry["role"] == "subject" and entry["cik"] in wanted:
                    found.add(entry["cik"])
            if not entries or all(e["updated"] < since for e in entries):
                break
    return found


# --------------------------------------------------------------------------
# Turning stored filings into events
# --------------------------------------------------------------------------


def _manager_label(manager: str | None, cik: int) -> str:
    return manager or starter_fund_name(cik) or f"CIK {cik}"


def _quarter_label(period) -> str:
    if period is None:
        return "an unknown quarter"
    return f"Q{(period.month - 1) // 3 + 1} {period.year}"


def _move_text(change: FundChange) -> str:
    name = change.symbol or change.issuer.title()
    if change.status == STATUS_NEW:
        return f"new {name} ({change.weight_now_pct:.1f}%)"
    if change.status == STATUS_SOLD_OUT:
        return f"sold out of {name}"
    sign = "+" if (change.change_pct or 0) >= 0 else ""
    return f"{name} {sign}{(change.change_pct or 0):.0f}%"


def fund_summary_event(change_set, filing_fact, manager: str, url: str) -> WatcherEvent:
    """One market-wide event per new 13F report: counts of what changed against
    the quarter before, and the biggest moves."""
    quarter = _quarter_label(change_set.period)
    if not change_set.has_comparison:
        headline = (
            f"{manager} filed its 13F for {quarter}: {change_set.holdings_count} positions "
            f"(long only, as of the quarter end; no earlier quarter stored to compare)"
        )
        moves: list[str] = []
    else:
        counts = {s: change_set.count(s) for s in (STATUS_NEW, STATUS_ADDED, STATUS_TRIMMED, STATUS_SOLD_OUT)}
        biggest = [c for c in change_set.changes if c.status in counts][:SUMMARY_TOP_MOVES]
        moves = [_move_text(c) for c in biggest]
        headline = (
            f"{manager} filed its 13F for {quarter}: {counts[STATUS_NEW]} new, {counts[STATUS_ADDED]} added, "
            f"{counts[STATUS_TRIMMED]} trimmed, {counts[STATUS_SOLD_OUT]} sold out"
            + (f". Biggest: {'; '.join(moves)}" if moves else "")
            + ". Long positions only, as of the quarter end."
        )
    return WatcherEvent(
        watcher=WATCHER_NAME,
        symbol=None,
        kind="fund_13f_filed",
        headline=headline,
        known_at=filing_fact.known_at,
        source_ref=url + "#summary",
        severity="notable",
        details={
            "manager": manager,
            "cik": change_set.cik,
            "period": change_set.period.isoformat() if change_set.period else None,
            "previous_period": change_set.previous_period.isoformat() if change_set.previous_period else None,
            "holdings": change_set.holdings_count,
            "new": change_set.count(STATUS_NEW),
            "added": change_set.count(STATUS_ADDED),
            "trimmed": change_set.count(STATUS_TRIMMED),
            "sold_out": change_set.count(STATUS_SOLD_OUT),
            "biggest_moves": moves,
            "filing_url": url,
        },
    )


def new_position_event(change: FundChange, change_set, filing_fact, manager: str, url: str) -> WatcherEvent:
    symbol = change.symbol or ""
    return WatcherEvent(
        watcher=WATCHER_NAME,
        symbol=symbol,
        kind="fund_new_position",
        headline=(
            f"{symbol}: {manager} opened a new position, {change.weight_now_pct:.1f}% of its reported portfolio "
            f"as of {_quarter_label(change_set.period)} (13F: long only, filed up to 45 days after the quarter end)"
        ),
        known_at=filing_fact.known_at,
        source_ref=f"{url}#new-{symbol}",
        severity="notable",
        details={
            "manager": manager,
            "cik": change_set.cik,
            "period": change_set.period.isoformat() if change_set.period else None,
            "shares": change.shares_now,
            "value": change.value_now,
            "weight_pct": round(change.weight_now_pct, 2),
            "filing_url": url,
        },
    )


def ownership_event(symbol: str, fact) -> WatcherEvent | None:
    """An event for one stored 13D/13G, or None for the ones that stay quiet
    (an amended 13G)."""
    p = fact.payload or {}
    form = p.get("form")
    if form == "SCHEDULE 13G/A":
        return None
    who = p.get("filer_name") or "A holder"
    pct = f"{p['percent']:g}%" if p.get("percent") is not None else "over 5%"
    issuer = p.get("issuer_name") or symbol
    if form == FORM_13D:
        headline = f"{symbol}: {who} filed a new Schedule 13D, holding {pct} of {issuer} (it may seek to influence the company)"
        severity = "notable"
    elif form == FORM_13D_A:
        headline = f"{symbol}: {who} amended its Schedule 13D, now {pct} of {issuer}"
        severity = "info"
    elif form == FORM_13G:
        headline = f"{symbol}: {who} filed a Schedule 13G, a passive holder with {pct} of {issuer}"
        severity = "info"
    else:
        return None
    return WatcherEvent(
        watcher=WATCHER_NAME,
        symbol=symbol,
        kind="ownership_5pct",
        headline=headline,
        known_at=fact.known_at,
        source_ref=fact.source_ref or p.get("accession"),
        severity=severity,
        details={
            "accession": p.get("accession"),
            "form": form,
            "filer": who,
            "percent": p.get("percent"),
            "shares": p.get("shares"),
            "event_date": p.get("event_date"),
            "purpose": p.get("purpose"),
        },
    )


_SEVERITY_RANK = {"urgent": 0, "notable": 1, "info": 2}


def _default_symbols() -> list[str]:
    from app.data_providers.universe import load_universe

    return [entry.symbol for entry in load_universe()]


def _is_blocked(exc: Exception) -> bool:
    text = str(exc)
    return "HTTP 403" in text or "HTTP 429" in text


class FundWatcher(Watcher):
    name = WATCHER_NAME
    description = (
        "New 13F reports from the funds you follow (what they hold, quarterly, long only, up to 45 days late) and new "
        "Schedule 13D/13G filings by 5% owners of watchlist companies. Everything is stored; a new fund position in a "
        "watchlist symbol or a new 13D can start an ordinary evaluation."
    )
    poll_interval_seconds = POLL_INTERVAL_SECONDS
    cooldown_seconds = COOLDOWN_SECONDS
    daily_fire_cap = DAILY_FIRE_CAP

    def __init__(
        self,
        *,
        client: SecClient | None = None,
        symbols: Callable[[], list[str]] | None = None,
        matcher: IssuerMatcher | None = None,
        feed_mode_min_symbols: int = FEED_MODE_MIN_SYMBOLS,
    ) -> None:
        self._client = client
        self._symbols = symbols or _default_symbols
        self._matcher = matcher
        self.feed_mode_min_symbols = feed_mode_min_symbols
        self.last_ownership_mode: str | None = None

    # -- 13F -------------------------------------------------------------

    def _poll_funds(self, context: WatcherContext, client: SecClient, watch: set[str]) -> list[WatcherEvent]:
        events: list[WatcherEvent] = []
        succeeded = errors = 0
        first_error: Exception | None = None
        blocked = False
        matcher = self._matcher
        for cik in followed_fund_ciks(context.settings.smart_money_followed_funds):
            try:
                if matcher is None:
                    matcher = default_matcher(client)
                fetched = fetch_new_13f(context.session, cik, client=client, matcher=matcher, today=context.now.date())
                succeeded += 1
                errors = 0
            except DataProviderError as exc:
                context.session.rollback()
                logger.warning("fund_filings: 13F of CIK %s failed: %s", cik, exc)
                first_error = first_error or exc
                errors += 1
                blocked = _is_blocked(exc)
                if blocked or errors >= MAX_CONSECUTIVE_ERRORS:
                    break
                continue
            # Only the newest filing that holds a table is announced: after an
            # outage several quarters can arrive at once, and one summary of the
            # latest is what is useful.
            announce = [r for r in fetched.results if r.is_new_filing and r.holdings > 0 and r.filing_fact is not None]
            if not announce:
                continue
            latest = max(announce, key=lambda r: r.filing_fact.known_at)
            if context.now - latest.filing_fact.known_at > FUND_EVENT_MAX_AGE:
                continue
            change_set = fund_changes_as_of(context.session, cik, context.now)
            if change_set is None:
                continue
            manager = _manager_label(fetched.manager_name, cik)
            url = (latest.filing_fact.source_ref or index_url(cik, latest.accession))
            events.append(fund_summary_event(change_set, latest.filing_fact, manager, url))
            new_positions = sorted(
                (
                    c
                    for c in change_set.changes
                    if c.status == STATUS_NEW and c.symbol and c.symbol in watch and c.share_type == "SH" and c.put_call is None
                ),
                key=lambda c: c.weight_now_pct,
                reverse=True,
            )
            for change in new_positions[:MAX_NEW_POSITION_EVENTS]:
                events.append(new_position_event(change, change_set, latest.filing_fact, manager, url))
        if first_error is not None and not events and (blocked or succeeded == 0):
            raise first_error
        return events

    # -- 13D / 13G -------------------------------------------------------

    def _watch_ciks(self, client: SecClient) -> dict[int, str]:
        out: dict[int, str] = {}
        for raw in self._symbols():
            symbol = raw.strip().upper()
            if not symbol or symbol.endswith("-USD") or symbol.startswith("^"):
                continue
            cik = resolve_cik(symbol, client=client)
            if cik is not None:
                out.setdefault(cik, symbol)
        return out

    @staticmethod
    def _since(context: WatcherContext) -> datetime:
        from app.watchers.models import WatcherState

        state = context.session.get(WatcherState, WATCHER_NAME)
        last = state.last_success_at if state is not None else None
        return (last - FEED_OVERLAP) if last is not None else context.now - FEED_FIRST_LOOKBACK

    def _poll_ownership(self, context: WatcherContext, client: SecClient) -> list[WatcherEvent]:
        ciks = self._watch_ciks(client)
        if not ciks:
            self.last_ownership_mode = None
            return []
        if len(ciks) > self.feed_mode_min_symbols:
            self.last_ownership_mode = "feed"
            candidates = _feed_subject_ciks(client, self._since(context), set(ciks))
        else:
            self.last_ownership_mode = "per_company"
            candidates = set(ciks)

        events: list[WatcherEvent] = []
        succeeded = errors = 0
        first_error: Exception | None = None
        blocked = False
        for cik in sorted(candidates):
            symbol = ciks[cik]
            try:
                fetched = fetch_ownership_filings(
                    context.session, cik, symbol, client=client, today=context.now.date(), lookback_days=OWNERSHIP_LOOKBACK_DAYS
                )
                succeeded += 1
                errors = 0
            except DataProviderError as exc:
                context.session.rollback()
                logger.warning("fund_filings: ownership filings of %s (CIK %s) failed: %s", symbol, cik, exc)
                first_error = first_error or exc
                errors += 1
                blocked = _is_blocked(exc)
                if blocked or errors >= MAX_CONSECUTIVE_ERRORS:
                    break
                continue
            for fact in fetched.new_facts:
                if context.now - fact.known_at > OWNERSHIP_EVENT_MAX_AGE:
                    continue
                event = ownership_event(symbol, fact)
                if event is not None:
                    events.append(event)
        if first_error is not None and not events and (blocked or succeeded == 0):
            raise first_error
        return events

    def _poll_fund_ownership(self, context: WatcherContext, client: SecClient, watch: set[str]) -> list[WatcherEvent]:
        """5% filings made BY followed funds: stored (the Activists view lists
        them), and an event only when the company is on the watchlist."""
        events: list[WatcherEvent] = []
        for cik in followed_fund_ciks(context.settings.smart_money_followed_funds):
            try:
                fetched = fetch_ownership_filings(
                    context.session,
                    cik,
                    None,
                    client=client,
                    today=context.now.date(),
                    lookback_days=OWNERSHIP_LOOKBACK_DAYS,
                    symbol_resolver=lambda issuer_cik: ticker_for_cik(issuer_cik, client=client),
                )
            except DataProviderError as exc:
                context.session.rollback()
                logger.warning("fund_filings: filings by CIK %s failed: %s", cik, exc)
                if _is_blocked(exc):
                    break
                continue
            for fact in fetched.new_facts:
                symbol = (fact.symbol or "").upper()
                if symbol and symbol in watch and context.now - fact.known_at <= OWNERSHIP_EVENT_MAX_AGE:
                    event = ownership_event(symbol, fact)
                    if event is not None:
                        events.append(event)
        return events

    # -- the poll --------------------------------------------------------

    def poll(self, context: WatcherContext) -> list[WatcherEvent]:
        client = self._client or get_sec_client()
        watch = {s.strip().upper() for s in self._symbols()}
        events: list[WatcherEvent] = []
        errors: list[Exception] = []
        for step in (
            lambda: self._poll_funds(context, client, watch),
            lambda: self._poll_ownership(context, client),
            lambda: self._poll_fund_ownership(context, client, watch),
        ):
            try:
                events.extend(step())
            except DataProviderError as exc:
                errors.append(exc)
        # One part failing must not hide what the others found; only a poll
        # that found nothing AND had a failure is reported as failed (the runner
        # records the error and backs off).
        if errors and not events:
            raise errors[0]
        events.sort(key=lambda e: (_SEVERITY_RANK.get(e.severity, 3), e.known_at))
        return events


def register_fund_watcher() -> None:
    """Install the watcher once, at application start-up (importing this module
    has no side effects)."""
    if get_watcher(WATCHER_NAME) is None:
        register_watcher(FundWatcher())
