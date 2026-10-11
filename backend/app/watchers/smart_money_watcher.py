"""The smart-money tracker watcher (NEW_PLAN section 6, steps 1-3).

Every poll it (1) STORES the newest filings as dated facts, then (2) scores the
symbols that have something new and (3) records a `SmartMoneyCandidate` for each
one at or above the bar. It never trades, never evaluates and never sets a
direction, size, stop or target: a candidate only says "this symbol deserves the
ordinary full evaluation", which a later phase runs through `generate_trade_plan`.

Two scopes, switched by the `smart_money_scope` setting, with no code change:

  * "universe" (the demo default): only the symbols of the effective universe
    (`load_universe`: STRATEGEIA_DEV_TICKERS, else the saved watchlist, else the
    catalogue). Their Form 4 / 8-K filings and 13D/G filings are fetched company by
    company; nothing market-wide is read from SEC.
  * "market_wide": SEC's "latest filings" feeds for Form 4, 8-K and 13D/G are read
    WITHOUT a watchlist filter, and every company found there is stored and scored.
    A company whose CIK maps to no ticker is stored as an `smart_money_unmapped`
    event and skipped: a symbol is never guessed.

In both scopes the House Clerk index (market-wide by nature) and the followed
funds' 13F filings are read; House rows for a ticker outside the universe are
stored but only scored in the market-wide scope.

It reuses the existing ingest code (`SecFilingsWatcher._process_company`,
`fetch_ownership_filings`, `ingest_ptr`, `fetch_new_13f`) and the shared
rate-limited SEC client, one request at a time from this one poller: it is not
a second concurrent SEC consumer. The existing watchers keep their behaviour; if
both run, the second finds each filing already stored (accession check) and
asks SEC for nothing more than the submissions index.

Candidate events carry no symbol on purpose: the watcher runner would otherwise
turn a symbol event into an evaluation, and phase 1 has none. The symbol is in
the headline and details. Wording is "reported", never "smart money knows".
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from app.data_providers.base import DataProviderError
from app.data_providers.house_disclosures import get_house_client, ingest_ptr, ingested_doc_ids, index_years, list_ptr_filings
from app.data_providers.sec_13dg import fetch_ownership_filings, ticker_for_cik
from app.data_providers.sec_13f import IssuerMatcher, default_matcher, fetch_new_13f, followed_fund_ciks
from app.data_providers.sec_client import SecClient, get_sec_client
from app.data_providers.sec_form4 import resolve_cik
from app.knowledge.congress_trades import trade_from_fact
from app.smartmoney.models import SmartMoneyCandidate
from app.smartmoney.tracker import (
    SCOPE_MARKET_WIDE,
    SCOPE_UNIVERSE,
    SOURCE_DELAY_NOTES,
    expire_stale_candidates,
    flag_candidate,
    fund_sets_at,
    smart_money_score_as_of,
)
from app.watchers import fund_watcher, sec_watcher
from app.watchers.base import Watcher, WatcherContext, WatcherEvent
from app.watchers.models import WatcherState
from app.watchers.registry import get_watcher, register_watcher

logger = logging.getLogger(__name__)

WATCHER_NAME = "smart_money"

# The tracker is a daily idea; a poll every few hours finds Form 4s the afternoon they are
# accepted without hammering SEC.
POLL_INTERVAL_SECONDS = 3 * 60 * 60
COOLDOWN_SECONDS = 0  # the re-flag guard (30 days / +2 points) is the real throttle
DAILY_FIRE_CAP = 50

# Feed paging (see sec_watcher / fund_watcher: newest first, stop at the last successful poll).
FEED_PAGE_SIZE = 100
FEED_MAX_PAGES = 6
FEED_OVERLAP = timedelta(hours=12)
FEED_FIRST_LOOKBACK = timedelta(days=2)
# The House index is read this far back, and at most this many new reports are downloaded per poll.
HOUSE_LOOKBACK_DAYS = 14
HOUSE_MAX_FILINGS_PER_POLL = 25
OWNERSHIP_LOOKBACK_DAYS = 14
MAX_CONSECUTIVE_ERRORS = 3

UNMAPPED_KIND = "smart_money_unmapped"
CANDIDATE_KIND = "smart_money_candidate"


def _default_symbols() -> list[str]:
    from app.data_providers.universe import load_universe

    return [entry.symbol for entry in load_universe()]


def _is_blocked(exc: Exception) -> bool:
    return "HTTP 403" in str(exc) or "HTTP 429" in str(exc)


def _plain(symbol: str) -> bool:
    return bool(symbol) and not symbol.endswith("-USD") and not symbol.startswith("^")


class SmartMoneyWatcher(Watcher):
    name = WATCHER_NAME
    description = (
        "Tracks reported insider purchases (Form 4), House trade reports, 5% holder filings (13D/13G), followed funds' 13F "
        "and 8-Ks, scores each symbol and records candidates for a full evaluation. Reports arrive up to 45 days after "
        "the trade; the score is a starting guess, not a proven signal. It never trades."
    )
    poll_interval_seconds = POLL_INTERVAL_SECONDS
    cooldown_seconds = COOLDOWN_SECONDS
    daily_fire_cap = DAILY_FIRE_CAP

    def __init__(
        self,
        *,
        client: SecClient | None = None,
        house_client: SecClient | None = None,
        symbols: Callable[[], list[str]] | None = None,
        matcher: IssuerMatcher | None = None,
    ) -> None:
        self._client = client
        self._house_client = house_client
        self._symbols = symbols or _default_symbols
        self._matcher = matcher
        # Filled by each poll, for tests and logs: the scope read and the symbols scored.
        self.last_scope: str | None = None
        self.last_scored: list[str] = []

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _since(context: WatcherContext) -> datetime:
        state = context.session.get(WatcherState, WATCHER_NAME)
        last = state.last_success_at if state is not None else None
        return (last - FEED_OVERLAP) if last is not None else context.now - FEED_FIRST_LOOKBACK

    def _universe_ciks(self, client: SecClient) -> dict[int, str]:
        out: dict[int, str] = {}
        for raw in self._symbols():
            symbol = raw.strip().upper()
            if not _plain(symbol):
                continue
            cik = resolve_cik(symbol, client=client)
            if cik is not None:
                out.setdefault(cik, symbol)
        return out

    @staticmethod
    def _feed_ciks(client: SecClient, since: datetime) -> set[int]:
        """Every issuer CIK in the market-wide feeds newer than `since`: Form 4 issuers, 8-K filers
        and 13D/13G subjects. No watchlist filter: that is the point of the market-wide scope."""
        found: set[int] = set()
        for form_type in sec_watcher.FEED_FORM_TYPES:
            for page in range(FEED_MAX_PAGES):
                entries = sec_watcher.parse_feed(
                    client.get_bytes(sec_watcher.FEED_URL.format(form=form_type, start=page * FEED_PAGE_SIZE, count=FEED_PAGE_SIZE))
                )
                for e in entries:
                    is_form4 = e["form"] in sec_watcher.sec_form4.FORM4_FORMS
                    is_8k = e["form"].startswith("8-K")
                    if (is_form4 and e["role"] == "issuer") or (is_8k and e["role"] in ("filer", "issuer")):
                        found.add(e["cik"])
                if not entries or all(e["updated"] < since for e in entries):
                    break
        for form_type in fund_watcher.FEED_FORM_TYPES:
            for page in range(FEED_MAX_PAGES):
                entries = fund_watcher.parse_ownership_feed(
                    client.get_bytes(fund_watcher.FEED_URL.format(form=form_type, start=page * FEED_PAGE_SIZE, count=FEED_PAGE_SIZE))
                )
                found.update(e["cik"] for e in entries if e["role"] == "subject")
                if not entries or all(e["updated"] < since for e in entries):
                    break
        return found

    # -- ingestion -------------------------------------------------------

    def _ingest_companies(
        self, context: WatcherContext, client: SecClient, ciks: dict[int, str]
    ) -> tuple[list[str], list[Exception], bool]:
        """Form 4, 8-K and 13D/G of each company, through the existing ingest code. Returns the
        symbols processed, the errors, and whether SEC turned us away."""
        sec = sec_watcher.SecFilingsWatcher(client=client)
        done: list[str] = []
        errors: list[Exception] = []
        in_a_row = 0
        for cik in sorted(ciks):
            symbol = ciks[cik]
            try:
                sec._process_company(context, client, cik, symbol)  # events are not used: the facts are the product
                fetch_ownership_filings(
                    context.session, cik, symbol, client=client, today=context.now.date(), lookback_days=OWNERSHIP_LOOKBACK_DAYS
                )
                done.append(symbol)
                in_a_row = 0
            except DataProviderError as exc:
                context.session.rollback()
                logger.warning("smart_money: %s (CIK %s) failed: %s", symbol, cik, exc)
                errors.append(exc)
                in_a_row += 1
                if _is_blocked(exc):
                    return done, errors, True
                if in_a_row >= MAX_CONSECUTIVE_ERRORS:
                    break
        return done, errors, False

    def _ingest_house(self, context: WatcherContext) -> tuple[set[str], list[Exception]]:
        """New House trade reports, any ticker. Returns the tickers of the new rows."""
        client = self._house_client or get_house_client()
        today = context.now.date()
        since = today - timedelta(days=HOUSE_LOOKBACK_DAYS)
        have = ingested_doc_ids(context.session)
        errors: list[Exception] = []
        found = []
        years = index_years(today)
        failed_years = 0
        for year in years:
            try:
                found.extend(f for f in list_ptr_filings(year, since, today, client=client) if f.doc_id not in have)
            except DataProviderError as exc:
                logger.warning("smart_money: House index for %s failed: %s", year, exc)
                errors.append(exc)
                failed_years += 1
        found.sort(key=lambda f: (f.filing_date, f.doc_id))
        tickers: set[str] = set()
        for filing in found[:HOUSE_MAX_FILINGS_PER_POLL]:
            try:
                result = ingest_ptr(context.session, filing, client=client)
            except DataProviderError as exc:
                context.session.rollback()
                logger.warning("smart_money: House report %s unreadable: %s", filing.doc_id, exc)
                errors.append(exc)
                continue
            for fact in result.new_trade_facts:
                trade = trade_from_fact(fact)
                if trade.symbol:
                    tickers.add(trade.symbol.upper())
        return tickers, errors

    def _ingest_funds(self, context: WatcherContext, client: SecClient) -> tuple[bool, list[Exception]]:
        """New 13F filings of the followed funds. Returns whether any new filing was stored."""
        errors: list[Exception] = []
        stored = False
        matcher = self._matcher
        for cik in followed_fund_ciks(context.settings.smart_money_followed_funds):
            try:
                if matcher is None:
                    matcher = default_matcher(client)
                fetched = fetch_new_13f(context.session, cik, client=client, matcher=matcher, today=context.now.date())
                stored = stored or any(r.is_new_filing for r in fetched.results)
            except DataProviderError as exc:
                context.session.rollback()
                logger.warning("smart_money: 13F of CIK %s failed: %s", cik, exc)
                errors.append(exc)
                if _is_blocked(exc):
                    break
        return stored, errors

    # -- the poll --------------------------------------------------------

    def poll(self, context: WatcherContext) -> list[WatcherEvent]:
        scope = SCOPE_MARKET_WIDE if context.settings.smart_money_scope == SCOPE_MARKET_WIDE else SCOPE_UNIVERSE
        self.last_scope = scope
        client = self._client or get_sec_client()
        events: list[WatcherEvent] = []
        errors: list[Exception] = []

        universe_ciks = self._universe_ciks(client)
        universe = {sym.strip().upper() for sym in self._symbols()}
        if scope == SCOPE_MARKET_WIDE:
            ciks, unmapped = self._market_wide_ciks(context, client, universe_ciks, events)
        else:
            ciks, unmapped = dict(universe_ciks), 0

        processed, company_errors, blocked = self._ingest_companies(context, client, ciks)
        errors += company_errors
        house_tickers: set[str] = set()
        funds_changed = False
        if not blocked:
            try:
                house_tickers, house_errors = self._ingest_house(context)
                errors += house_errors
            except DataProviderError as exc:
                errors.append(exc)
            funds_changed, fund_errors = self._ingest_funds(context, client)
            errors += fund_errors

        # What to score: every symbol with something new, plus (universe scope) the whole universe,
        # which is small by definition and also catches an old filing the window now includes.
        if scope == SCOPE_UNIVERSE:
            to_score = set(universe)
        else:
            to_score = set(processed) | house_tickers
        events += self._score_and_flag(context, sorted(s for s in to_score if _plain(s)), scope, funds_changed)

        if errors and not events and not processed and unmapped == 0:
            raise errors[0]
        return events

    def _market_wide_ciks(
        self, context: WatcherContext, client: SecClient, universe_ciks: dict[int, str], events: list[WatcherEvent]
    ) -> tuple[dict[int, str], int]:
        """Feed CIKs -> tickers. An unmapped CIK becomes a stored info event and is skipped."""
        mapped: dict[int, str] = dict(universe_ciks)
        unmapped = 0
        for cik in sorted(self._feed_ciks(client, self._since(context))):
            if cik in mapped:
                continue
            symbol = ticker_for_cik(cik, client=client)
            if not symbol:
                unmapped += 1
                events.append(
                    WatcherEvent(
                        watcher=WATCHER_NAME,
                        symbol=None,
                        kind=UNMAPPED_KIND,
                        headline=f"A filing by CIK {cik} could not be matched to a ticker and was skipped (never guessed)",
                        known_at=context.now,
                        source_ref=f"unmapped-cik-{cik}",
                        severity="info",
                        details={"cik": cik},
                    )
                )
                continue
            mapped[cik] = symbol.upper()
        return mapped, unmapped

    def _score_and_flag(
        self, context: WatcherContext, symbols: list[str], scope: str, funds_changed: bool
    ) -> list[WatcherEvent]:
        settings = context.settings
        expire_stale_candidates(context.session, context.now)
        fund_sets = fund_sets_at(context.session, settings, context.now)
        # A new 13F can make a symbol of a followed fund's changes newly relevant.
        if funds_changed and scope == SCOPE_MARKET_WIDE:
            for _label, change_set in fund_sets.values():
                for c in change_set.changes:
                    if c.symbol and c.status in ("new", "added") and c.symbol not in symbols:
                        symbols.append(c.symbol)
        events: list[WatcherEvent] = []
        scored: list[str] = []
        for symbol in symbols:
            result = smart_money_score_as_of(context.session, symbol, context.now, settings, fund_sets=fund_sets)
            scored.append(symbol)
            candidate = flag_candidate(context.session, result, settings, scope=scope)
            if candidate is not None:
                events.append(candidate_event(candidate))
        self.last_scored = scored
        return events


def candidate_event(candidate: SmartMoneyCandidate) -> WatcherEvent:
    parts = "; ".join(f"{c['reason']} ({c['points']:+g})" for c in candidate.components[:4])
    sources = sorted({c["source"] for c in candidate.components})
    return WatcherEvent(
        watcher=WATCHER_NAME,
        symbol=None,  # see the module note: an event with a symbol would start an evaluation
        kind=CANDIDATE_KIND,
        headline=(
            f"{candidate.symbol}: smart-money score {candidate.score:g}, reported filings as of "
            f"{candidate.scored_at:%Y-%m-%d} (not a verdict; reports can be days to weeks late). {parts}"
        ),
        known_at=candidate.first_trigger_known_at or candidate.scored_at,
        source_ref=f"smart-money-candidate-{candidate.id}",
        severity="notable",
        details={
            "symbol": candidate.symbol,
            "candidate_id": candidate.id,
            "score": candidate.score,
            "components": candidate.components,
            "delay_notes": {s: SOURCE_DELAY_NOTES[s] for s in sources if s in SOURCE_DELAY_NOTES},
        },
    )


def register_smart_money_watcher() -> None:
    """Install the watcher once. Called at application start-up, not at import."""
    if get_watcher(WATCHER_NAME) is None:
        register_watcher(SmartMoneyWatcher())


__all__ = ["SmartMoneyWatcher", "register_smart_money_watcher", "candidate_event"]
