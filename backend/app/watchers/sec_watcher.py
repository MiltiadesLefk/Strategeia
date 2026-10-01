"""The SEC filings watcher: new Form 4 and 8-K filings for the watchlist.

Every poll finds filings the app has not stored yet, stores them as dated facts
(Form 4 transaction rows through `sec_form4`, 8-Ks through `sec_8k`, each with
its SEC acceptance time as `known_at`), and only then returns events. The runner
records the events after that, so the facts exist whatever happens to an alert.

Only a few things become events. Everything else is stored and stays quiet:

  * an open-market insider BUY worth at least INSIDER_BUY_ALERT_VALUE in one
    filing (purchases minus sales in that filing), or a CLUSTER of purchases by
    at least two different insiders completed by a new filing;
  * an 8-K that lists one of the INTERESTING_8K_ITEMS.

Insider SALES never alert, on purpose. Insiders sell for many reasons (taxes,
diversifying, pre-set plans) that say little about the company, so reading a
sale as bad news is mostly noise. Sales are still stored, and the Smart Money
page shows them.

Two ways to find new filings, chosen by the size of the watchlist:

  * up to FEED_MODE_MIN_SYMBOLS companies: ask each company's submissions index
    (one request per company per poll);
  * more: read SEC's market-wide "latest filings" feeds (a few requests), keep
    the entries whose company is on the watchlist, and only then ask the
    submissions index of those few companies. 500 per-company requests every
    five minutes would be wasteful when the feed lists the same filings at once.

"Already seen" is read from the stored facts themselves (an accession number
with stored rows is not fetched again), so there is no separate cursor to lose
or corrupt. The saved watcher state is used for one thing: the feed is read back
as far as the last successful poll.

Failures: a request that SEC refuses or rate-limits ends the poll. With nothing
found so far it raises (the runner records the error and backs off); a poll that
already has events returns them, so a late failure does not lose them.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.data_providers import sec_form4
from app.data_providers.base import DataProviderError
from app.data_providers.sec_8k import (
    FORM_8K,
    Filing8K,
    filings_from_index,
    index_url,
    ingest_8k_filing,
    ingested_8k_accessions,
    item_title,
)
from app.data_providers.sec_client import SecClient, get_sec_client
from app.data_providers.sec_form4 import (
    FORM_4,
    SUBMISSIONS_URL,
    Form4Filing,
    ingest_form4_filing,
    ingested_accessions,
    resolve_cik,
)
from app.knowledge.insider_trades import insider_clusters_as_of
from app.watchers.base import Watcher, WatcherContext, WatcherEvent
from app.watchers.models import WatcherState
from app.watchers.registry import get_watcher, register_watcher

logger = logging.getLogger(__name__)

WATCHER_NAME = "sec_filings"

# Poll every five minutes: Form 4s are due within two business days and 8-Ks
# within four, so minutes of delay cost little and SEC's rate limit is respected.
POLL_INTERVAL_SECONDS = 300
# One alert per company per six hours: filings arrive in bursts (several insiders
# file the same afternoon) and one alert covers the burst; the rest are recorded.
COOLDOWN_SECONDS = 6 * 60 * 60
DAILY_FIRE_CAP = 20

# Up to this many companies on the watchlist: per-company submissions requests.
# Above it: the market-wide feeds. At the 5 requests per second the shared client
# allows, 40 companies take about eight seconds, which is still cheap; beyond
# that the feed (a handful of requests) is clearly cheaper.
FEED_MODE_MIN_SYMBOLS = 40
# Each feed page holds 100 entries, newest first. Form 4s are the busy feed
# (hundreds an hour at peak), so paging stops at the last successful poll or at
# this many pages, whichever comes first.
FEED_PAGE_SIZE = 100
FEED_MAX_PAGES = 6
# The feed is read back this far before the last successful poll, in case an
# entry appeared late in the listing.
FEED_OVERLAP = timedelta(minutes=30)
# With no previous successful poll the feed is read back this far.
FEED_FIRST_LOOKBACK = timedelta(hours=6)
FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&company=&dateb=&owner=include"
    "&start={start}&count={count}&output=atom"
)
FEED_FORM_TYPES = ("4", "8-K")

# Per-company mode (and the follow-up in feed mode) looks at filings filed in
# the last few days, so a filing missed by an outage is still caught.
LOOKBACK_DAYS = 7
# A filing accepted longer ago than this is stored but never alerts: it is no
# longer news, and it keeps the first run on an empty database from announcing a
# week of old filings.
EVENT_MAX_AGE = timedelta(hours=48)

# One filing's open-market purchases minus its sales must reach this to alert.
INSIDER_BUY_ALERT_VALUE = 100_000.0
# A cluster is looked for in purchases known in the last month.
CLUSTER_LOOKBACK_DAYS = 30

# 8-K item codes worth an alert: results, material agreements starting or
# ending, bankruptcy, completed deals, restructuring costs, listing trouble,
# auditor changes, restated financials, a change of control and officer changes.
# Item 8.01 ("other events") and the routine exhibits/FD items are left out: far
# too common to be worth an alert.
INTERESTING_8K_ITEMS = frozenset(
    {"1.01", "1.02", "1.03", "2.01", "2.02", "2.05", "2.06", "3.01", "4.01", "4.02", "5.01", "5.02"}
)
# Of those, the ones that are urgent rather than just notable.
URGENT_8K_ITEMS = frozenset({"1.03", "3.01", "4.02"})

# Consecutive failed companies after which a poll gives up for this round.
MAX_CONSECUTIVE_ERRORS = 3

_FEED_TITLE_RE = re.compile(r"^(?P<form>\S+) - .*\((?P<cik>\d{4,10})\) \((?P<role>[A-Za-z ]+)\)\s*$")
_FORBIDDEN_XML_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_ATOM = "{http://www.w3.org/2005/Atom}"


# --------------------------------------------------------------------------
# The market-wide feed
# --------------------------------------------------------------------------


def parse_feed(xml: bytes) -> list[dict]:
    """Entries of one Atom page -> [{form, cik, role, accession, updated}], where
    `updated` is naive UTC. Entries that do not parse are skipped."""
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
        updated_raw = entry.findtext(f"{_ATOM}updated") or ""
        accession = entry_id.split("accession-number=")[-1] if "accession-number=" in entry_id else ""
        if not match or not accession:
            continue
        try:
            updated = datetime.fromisoformat(updated_raw)
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


def _feed_ciks(client: SecClient, since: datetime, wanted: set[int]) -> set[int]:
    """CIKs on `wanted` that have a Form 4 (as the issuer) or an 8-K (as the
    filer) newer than `since` in the market-wide feeds."""
    found: set[int] = set()
    for form_type in FEED_FORM_TYPES:
        for page in range(FEED_MAX_PAGES):
            entries = parse_feed(
                client.get_bytes(FEED_URL.format(form=form_type, start=page * FEED_PAGE_SIZE, count=FEED_PAGE_SIZE))
            )
            for entry in entries:
                is_form4 = entry["form"] in sec_form4.FORM4_FORMS
                is_8k = entry["form"].startswith("8-K")
                # A Form 4 appears twice, once per party; only the issuer's entry names the company.
                if (is_form4 and entry["role"] == "issuer") or (is_8k and entry["role"] in ("filer", "issuer")):
                    if entry["cik"] in wanted:
                        found.add(entry["cik"])
            # Newest first: once a whole page is older than the cut-off there is nothing more to find.
            if not entries or all(e["updated"] < since for e in entries):
                break
    return found


# --------------------------------------------------------------------------
# Turning stored filings into events
# --------------------------------------------------------------------------


def _who(payload: dict) -> str:
    name = payload.get("owner_name") or "An insider"
    role = payload.get("officer_title") or (
        "director" if payload.get("is_director") else "10% owner" if payload.get("is_ten_percent_owner") else None
    )
    return f"{name} ({role})" if role else name


def insider_buy_event(symbol: str, filing: Form4Filing, new_facts: list) -> WatcherEvent | None:
    """An event when one filing's open-market purchases minus its sales reach the
    alert value; None otherwise (including a filing of only sales)."""
    rows = [f.payload or {} for f in new_facts]
    rows = [p for p in rows if p.get("table", "non_derivative") == "non_derivative"]
    bought = sum((p.get("value") or 0.0) for p in rows if p.get("code") == "P")
    sold = sum((p.get("value") or 0.0) for p in rows if p.get("code") == "S")
    net = bought - sold
    if bought <= 0 or net < INSIDER_BUY_ALERT_VALUE:
        return None
    buy_rows = [p for p in rows if p.get("code") == "P"]
    return WatcherEvent(
        watcher=WATCHER_NAME,
        symbol=symbol,
        kind="insider_buy",
        headline=f"{symbol}: {_who(buy_rows[0])} bought ${net:,.0f} of stock on the open market",
        known_at=filing.accepted_at,
        source_ref=index_url(filing.cik, filing.accession),
        severity="notable",
        details={
            "accession": filing.accession,
            "form": filing.form,
            "net_value": round(net, 2),
            "insiders": sorted({p.get("owner_name") or "" for p in buy_rows}),
            "filing_url": filing.url,
        },
    )


def cluster_event(symbol: str, filing: Form4Filing, cluster) -> WatcherEvent:
    return WatcherEvent(
        watcher=WATCHER_NAME,
        symbol=symbol,
        kind="insider_cluster",
        headline=(
            f"{symbol}: {cluster.insider_count} insiders bought on the open market between "
            f"{cluster.start_date} and {cluster.end_date} (${cluster.total_value:,.0f} priced)"
        ),
        known_at=filing.accepted_at,
        # A different reference from the single-buy event for the same filing, so
        # neither is mistaken for a repeat of the other.
        source_ref=index_url(filing.cik, filing.accession) + "#cluster",
        severity="urgent",
        details={
            "insiders": list(cluster.insiders),
            "insider_count": cluster.insider_count,
            "trade_count": cluster.trade_count,
            "total_value": round(cluster.total_value, 2),
            "unpriced_trades": cluster.unpriced_trades,
            "roles": list(cluster.roles),
            "any_10b5_1": cluster.any_10b5_1,
            "completed_by": filing.accession,
        },
    )


def filing_8k_event(symbol: str, filing: Filing8K) -> WatcherEvent | None:
    interesting = [c for c in filing.items if c in INTERESTING_8K_ITEMS]
    if not interesting:
        return None
    urgent = any(c in URGENT_8K_ITEMS for c in interesting)
    titles = "; ".join(f"{c} {item_title(c)}" for c in interesting)
    return WatcherEvent(
        watcher=WATCHER_NAME,
        symbol=symbol,
        kind="filing_8k",
        headline=f"{symbol} filed an 8-K: {titles}",
        known_at=filing.accepted_at,
        source_ref=filing.index_url,
        severity="urgent" if urgent else "notable",
        details={
            "accession": filing.accession,
            "form": filing.form,
            "items": list(filing.items),
            "interesting_items": interesting,
            "report_date": filing.report_date.isoformat() if filing.report_date else None,
            "primary_document_url": filing.url,
        },
    )


_SEVERITY_RANK = {"urgent": 0, "notable": 1, "info": 2}


# --------------------------------------------------------------------------
# The watcher
# --------------------------------------------------------------------------


def _default_symbols() -> list[str]:
    from app.data_providers.universe import load_universe

    return [entry.symbol for entry in load_universe()]


def _is_blocked(exc: Exception) -> bool:
    """SEC refused us (address blocked, bad User-Agent, still rate limited after
    the client's retries): stop asking for the rest of this round."""
    text = str(exc)
    return "HTTP 403" in text or "HTTP 429" in text


class SecFilingsWatcher(Watcher):
    name = WATCHER_NAME
    description = (
        "New SEC filings for the watchlist: insider purchases (Form 4) and important company announcements "
        "(8-K). Everything is stored; only large or clustered insider buys and key 8-K items alert."
    )
    poll_interval_seconds = POLL_INTERVAL_SECONDS
    cooldown_seconds = COOLDOWN_SECONDS
    daily_fire_cap = DAILY_FIRE_CAP

    def __init__(
        self,
        *,
        client: SecClient | None = None,
        symbols: Callable[[], list[str]] | None = None,
        feed_mode_min_symbols: int = FEED_MODE_MIN_SYMBOLS,
    ) -> None:
        self._client = client
        self._symbols = symbols or _default_symbols
        self.feed_mode_min_symbols = feed_mode_min_symbols
        # Which way the last poll looked for filings ("per_company" | "feed"), for the tests and logs.
        self.last_mode: str | None = None

    # -- helpers ---------------------------------------------------------

    def _ciks(self, client: SecClient) -> dict[int, str]:
        """CIK -> symbol for the watchlist's SEC registrants (crypto pairs, indexes
        and symbols the ticker file does not list are skipped)."""
        out: dict[int, str] = {}
        for raw in self._symbols():
            symbol = raw.strip().upper()
            if not symbol or symbol.endswith("-USD") or symbol.startswith("^"):
                continue
            cik = resolve_cik(symbol, client=client)
            if cik is not None:
                out.setdefault(cik, symbol)  # GOOG and GOOGL share a CIK: store under the first
        return out

    @staticmethod
    def _since(context: WatcherContext) -> datetime:
        state = context.session.get(WatcherState, WATCHER_NAME)
        last = state.last_success_at if state is not None else None
        return (last - FEED_OVERLAP) if last is not None else context.now - FEED_FIRST_LOOKBACK

    @staticmethod
    def _fresh(context: WatcherContext, accepted_at: datetime) -> bool:
        return context.now - accepted_at <= EVENT_MAX_AGE

    # -- the poll --------------------------------------------------------

    def poll(self, context: WatcherContext) -> list[WatcherEvent]:
        client = self._client or get_sec_client()
        ciks = self._ciks(client)
        if not ciks:
            self.last_mode = None
            return []
        if len(ciks) > self.feed_mode_min_symbols:
            self.last_mode = "feed"
            candidates = _feed_ciks(client, self._since(context), set(ciks))
        else:
            self.last_mode = "per_company"
            candidates = set(ciks)

        events: list[WatcherEvent] = []
        succeeded = errors_in_a_row = 0
        first_error: Exception | None = None
        blocked = False
        for cik in sorted(candidates):
            symbol = ciks[cik]
            try:
                events.extend(self._process_company(context, client, cik, symbol))
                succeeded += 1
                errors_in_a_row = 0
            except DataProviderError as exc:
                context.session.rollback()
                logger.warning("sec_filings: %s (CIK %s) failed: %s", symbol, cik, exc)
                first_error = first_error or exc
                errors_in_a_row += 1
                blocked = _is_blocked(exc)
                if blocked or errors_in_a_row >= MAX_CONSECUTIVE_ERRORS:
                    break
        # Nothing to return: say so loudly if SEC turned us away or nothing worked at
        # all (the runner records the error and backs off). One flaky company among
        # many healthy ones is only logged.
        if first_error is not None and not events and (blocked or succeeded == 0):
            raise first_error
        return events

    def _process_company(self, context: WatcherContext, client: SecClient, cik: int, symbol: str) -> list[WatcherEvent]:
        index = client.get_json(SUBMISSIONS_URL.format(cik=cik))
        if not isinstance(index, dict):
            raise DataProviderError(f"unexpected submissions payload for CIK {cik}")
        since_day = context.now.date() - timedelta(days=LOOKBACK_DAYS)
        recent = (index.get("filings") or {}).get("recent") or {}
        form4s = [f for f in sec_form4._filings_from_columns(cik, recent) if f.filing_date >= since_day]
        eight_ks = filings_from_index(cik, index, since=since_day)

        events: list[WatcherEvent] = []
        have4 = ingested_accessions(context.session, symbol)
        new4_times: dict[datetime, Form4Filing] = {}
        any_new_buy = False
        for filing in sorted(form4s, key=lambda f: (f.accepted_at, f.accession)):
            if filing.accession in have4:
                continue
            try:
                result = ingest_form4_filing(context.session, symbol, filing, client=client)
            except DataProviderError as exc:
                if _is_blocked(exc):
                    raise
                context.session.rollback()
                logger.warning("sec_filings: Form 4 %s for %s unreadable: %s", filing.accession, symbol, exc)
                continue
            new4_times[filing.accepted_at] = filing
            if filing.form == FORM_4 and self._fresh(context, filing.accepted_at):
                event = insider_buy_event(symbol, filing, result.new_facts)
                if event is not None:
                    events.append(event)
            any_new_buy = any_new_buy or any((f.payload or {}).get("code") == "P" for f in result.new_facts)

        if any_new_buy:
            for cluster in insider_clusters_as_of(
                context.session, symbol, context.now, window_days=CLUSTER_LOOKBACK_DAYS
            ):
                completing = new4_times.get(cluster.visible_from)
                if completing is not None and self._fresh(context, completing.accepted_at):
                    events.append(cluster_event(symbol, completing, cluster))

        have8 = ingested_8k_accessions(context.session, symbol)
        for filing8 in sorted(eight_ks, key=lambda f: (f.accepted_at, f.accession)):
            if filing8.accession in have8:
                continue
            ingest_8k_filing(context.session, symbol, filing8)
            if filing8.form == FORM_8K and self._fresh(context, filing8.accepted_at):
                event = filing_8k_event(symbol, filing8)
                if event is not None:
                    events.append(event)

        events.sort(key=lambda e: (_SEVERITY_RANK.get(e.severity, 3), e.known_at))
        return events


def register_sec_watcher() -> None:
    """Install the watcher once. Called at application start-up, not at import,
    so importing this module has no side effects."""
    if get_watcher(WATCHER_NAME) is None:
        register_watcher(SecFilingsWatcher())
