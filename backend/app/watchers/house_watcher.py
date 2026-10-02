"""The House trade-reports watcher: new stock-trade reports from House members.

Every poll reads the Clerk's yearly index, finds trade reports filed in the last
couple of weeks that are not stored yet, reads each one and stores its rows as
dated facts (`known_at` = the filing date). Only then does it return events; the
runner records those afterwards, so the facts exist whatever happens to an alert.

Only a few things become events. Everything else is stored and stays quiet:

  * a stock PURCHASE by a followed member (the Settings follow list; everyone when
    it is set to "all") in a symbol on the watchlist;
  * a CLUSTER: a new report that makes at least two different followed members'
    purchases of the same watchlist stock within 30 days of each other.

Sales never alert, on purpose: members sell for taxes, rebalancing and liquidity
far more often than for a view, and the report says nothing about why. They are
still stored and shown on the Smart Money page.

What an event means, and does not: the report is filed up to 45 days after the
trade, so an alert says "this was reported", not "this just happened". The amount
is a range. A spouse's or dependent child's trade is included and the alert says
whose it was. An alert never changes a trade's direction, size or levels; with the
watchers action set to re-evaluate it asks for the ordinary full evaluation of the
symbol, which applies every normal gate. Events are produced only for watchlist
symbols, so a trade in some company the user does not follow never triggers an
evaluation.

A report filed longer ago than EVENT_MAX_AGE_DAYS is stored but never alerts: it is
no longer news, and it keeps the first run on an empty database from announcing a
fortnight of old reports. "Already seen" is read from the stored facts (a document
id with a stored record is not fetched again), so there is no separate cursor to
lose or corrupt.

Failures: the Clerk's site is a public service without a stated limit and the
client is held to one request a second. A report that cannot be downloaded is
skipped and retried next poll. A poll that finds nothing but errors raises (the
runner records the error and backs off); a poll that stored something returns its
events.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta

from app.data_providers.base import DataProviderError
from app.data_providers.house_disclosures import (
    HouseFiling,
    get_house_client,
    index_years,
    ingest_ptr,
    ingested_doc_ids,
    list_ptr_filings,
)
from app.data_providers.sec_client import SecClient
from app.knowledge.congress_trades import (
    EQUITY_ASSET_TYPES,
    CongressTrade,
    congress_clusters_as_of,
    member_key,
    trade_from_fact,
)
from app.services.congress_service import followed_members
from app.watchers.base import Watcher, WatcherContext, WatcherEvent
from app.watchers.registry import get_watcher, register_watcher

logger = logging.getLogger(__name__)

WATCHER_NAME = "house_ptr"

# Reports arrive a few at a time through the day and nothing here is urgent (they
# are weeks old by definition), so a few polls a day are plenty.
POLL_INTERVAL_SECONDS = 6 * 60 * 60
# One alert per symbol per day: a member often files several lines for one stock.
COOLDOWN_SECONDS = 24 * 60 * 60
DAILY_FIRE_CAP = 10

# Index entries filed in the last this-many days are looked at (so a report missed
# by an outage is still caught).
LOOKBACK_DAYS = 14
# A report filed longer ago than this stores quietly (see the module note).
EVENT_MAX_AGE_DAYS = 5
# At most this many new reports are downloaded per poll (one request a second);
# the rest wait for the next poll.
MAX_FILINGS_PER_POLL = 25
# Clusters are looked for in reports filed in this many days.
CLUSTER_LOOKBACK_DAYS = 45
CLUSTER_ADVICE = "Several members buying within a month is a stronger sign than one."

_SEVERITY_RANK = {"urgent": 0, "notable": 1, "info": 2}


def _default_symbols() -> list[str]:
    from app.data_providers.universe import load_universe

    return [entry.symbol for entry in load_universe()]


def _owner_note(trade: CongressTrade) -> str:
    return "" if trade.owner == "self" else f" ({trade.owner}'s trade)"


def _range_text(trade: CongressTrade) -> str:
    return trade.amount_text or "an unstated amount"


def purchase_event(symbol: str, filing: HouseFiling, trades: list[CongressTrade]) -> WatcherEvent:
    """One event for one filing's stock purchases of `symbol`."""
    first = trades[0]
    delay = first.filing_delay_days
    delay_text = f", reported {delay} days after the trade" if delay is not None and delay >= 0 else ""
    amounts = "; ".join(sorted({_range_text(t) for t in trades}))
    return WatcherEvent(
        watcher=WATCHER_NAME,
        symbol=symbol,
        kind="congress_buy",
        headline=(
            f"{symbol}: {first.member} reported buying{_owner_note(first)} in the range {amounts} "
            f"(traded {first.trade_date}, filed {first.filed_date}{delay_text})"
        ),
        known_at=first.known_at,
        # One per filing and symbol: a second line for the same stock in the same
        # report is part of the same event, not a new one.
        source_ref=f"{filing.url}#{symbol}",
        severity="notable",
        details={
            "doc_id": filing.doc_id,
            "member": first.member,
            "owner": first.owner,
            "trade_count": len(trades),
            "amount_ranges": sorted({_range_text(t) for t in trades}),
            "trade_dates": sorted({t.trade_date.isoformat() for t in trades if t.trade_date}),
            "filed_date": first.filed_date.isoformat() if first.filed_date else None,
            "filing_url": filing.url,
        },
    )


def cluster_event(symbol: str, filing: HouseFiling, cluster) -> WatcherEvent:
    return WatcherEvent(
        watcher=WATCHER_NAME,
        symbol=symbol,
        kind="congress_cluster",
        headline=(
            f"{symbol}: {cluster.member_count} members of Congress reported buying between "
            f"{cluster.start_date} and {cluster.end_date} ({', '.join(cluster.members)}). {CLUSTER_ADVICE}"
        ),
        known_at=cluster.visible_from,
        # A different reference from the single-buy event for the same filing, so
        # neither is mistaken for a repeat of the other.
        source_ref=f"{filing.url}#{symbol}-cluster",
        severity="urgent",
        details={
            "members": list(cluster.members),
            "member_count": cluster.member_count,
            "trade_count": cluster.trade_count,
            "start_date": cluster.start_date.isoformat(),
            "end_date": cluster.end_date.isoformat(),
            "completed_by": filing.doc_id,
        },
    )


class HouseWatcher(Watcher):
    name = WATCHER_NAME
    description = (
        "New stock-trade reports from members of the House. Everything is stored; only stock purchases by the "
        "members you follow, in watchlist symbols, alert. Reports can be up to 45 days after the trade."
    )
    poll_interval_seconds = POLL_INTERVAL_SECONDS
    cooldown_seconds = COOLDOWN_SECONDS
    daily_fire_cap = DAILY_FIRE_CAP

    def __init__(
        self,
        *,
        client: SecClient | None = None,
        symbols: Callable[[], list[str]] | None = None,
    ) -> None:
        self._client = client
        self._symbols = symbols or _default_symbols

    def _new_filings(self, context: WatcherContext, client: SecClient) -> list[HouseFiling]:
        today = context.now.date()
        since = today - timedelta(days=LOOKBACK_DAYS)
        have = ingested_doc_ids(context.session)
        found: list[HouseFiling] = []
        errors: list[Exception] = []
        years = index_years(today)
        for year in years:
            try:
                found.extend(f for f in list_ptr_filings(year, since, today, client=client) if f.doc_id not in have)
            except DataProviderError as exc:
                logger.warning("house_ptr: index for %s failed: %s", year, exc)
                errors.append(exc)
        if errors and len(errors) == len(years):
            raise errors[0]
        found.sort(key=lambda f: (f.filing_date, f.doc_id))
        return found[:MAX_FILINGS_PER_POLL]

    @staticmethod
    def _fresh(context: WatcherContext, filed: date | None) -> bool:
        return filed is not None and (context.now.date() - filed).days <= EVENT_MAX_AGE_DAYS

    def poll(self, context: WatcherContext) -> list[WatcherEvent]:
        client = self._client or get_house_client()
        watchlist = {s.strip().upper() for s in self._symbols()}
        followed = followed_members(context.settings)
        followed_keys = None if followed is None else {member_key(m) for m in followed}

        events: list[WatcherEvent] = []
        succeeded = 0
        first_error: Exception | None = None
        for filing in self._new_filings(context, client):
            try:
                result = ingest_ptr(context.session, filing, client=client)
            except DataProviderError as exc:
                context.session.rollback()
                logger.warning("house_ptr: report %s unreadable: %s", filing.doc_id, exc)
                first_error = first_error or exc
                continue
            succeeded += 1
            if not self._fresh(context, filing.filing_date):
                continue
            events.extend(self._events_for(context, filing, result.new_trade_facts, watchlist, followed_keys, followed))
        if first_error is not None and not events and succeeded == 0:
            raise first_error
        events.sort(key=lambda e: (_SEVERITY_RANK.get(e.severity, 3), e.known_at))
        return events

    @staticmethod
    def _events_for(
        context: WatcherContext,
        filing: HouseFiling,
        new_facts: list,
        watchlist: set[str],
        followed_keys: set[str] | None,
        followed: list[str] | None,
    ) -> list[WatcherEvent]:
        buys: dict[str, list[CongressTrade]] = {}
        for fact in new_facts:
            trade = trade_from_fact(fact)
            if (
                trade.side == "buy"
                and trade.asset_type in EQUITY_ASSET_TYPES
                and trade.symbol
                and trade.symbol in watchlist
                and (followed_keys is None or trade.member_key in followed_keys)
            ):
                buys.setdefault(trade.symbol, []).append(trade)
        events: list[WatcherEvent] = []
        for symbol, trades in sorted(buys.items()):
            events.append(purchase_event(symbol, filing, trades))
            for cluster in congress_clusters_as_of(
                context.session, symbol, context.now, window_days=CLUSTER_LOOKBACK_DAYS, members=followed
            ):
                # Only a cluster this very filing completed is news.
                if cluster.visible_from == trades[0].known_at:
                    events.append(cluster_event(symbol, filing, cluster))
        return events


def register_house_watcher() -> None:
    """Install the watcher once. Called at application start-up, not at import,
    so importing this module has no side effects."""
    if get_watcher(WATCHER_NAME) is None:
        register_watcher(HouseWatcher())

