"""Reading Congress stock-trade reports as they were known at a moment in time.

The facts are written by `app.data_providers.house_disclosures` (one
`congress_trade` fact per row of a trade report, plus one `congress_filing` fact
per report). `known_at` is the FILING date (end of that day in New York), and
`effective_at` is the trade date. Everything here reads through
`facts_known_as_of`, so a trade whose report was filed after the cutoff is
invisible even if the trade itself happened earlier. This is the whole point: a
member may report a trade up to 45 days after making it (some are later still), so
a test or a signal that dated the trade by its trade date would be using
information that did not exist yet. Inside `with as_of(t):` the `as_of` argument
can be left out and defaults to the simulated moment.

Windows ("the last 45 days") are measured on `known_at`, i.e. on reports filed in
that window, the same way the insider readers count filings, and for the same
reason: it is when a trader could first have reacted.

Amounts are ranges. Nothing here adds a range up into a single dollar figure;
`amount_low`/`amount_high` are kept and a range total is a (low, high) pair whose
high end is None when any part of it is open-ended ("Over $50,000,000").

What counts as a buy for clusters and the signal: a purchase of a stock (the
form's asset code ST, which includes ETFs). Options, bonds, funds, trusts and
private assets are listed but never counted: a bought option may be a call or a
put and a fund line carries no company.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median

from sqlmodel import Session

from app.knowledge import FactKind, KnownFact, current_as_of, facts_known_as_of, to_naive_utc

CONGRESS_FILING_KIND = "congress_filing"

DEFAULT_WINDOW_DAYS = 90
# A cluster is several different members buying the same stock within this many
# days of each other (by trade date): more telling than one member buying,
# because it is independent.
CLUSTER_DAYS = 30
CLUSTER_MIN_MEMBERS = 2
# Only stocks are counted by clusters and the signal (see the module note).
EQUITY_ASSET_TYPES = ("ST",)

_KEY_STRIP_RE = re.compile(r"[^a-z0-9]+")


def member_key(name: str | None) -> str:
    """A name reduced to lowercase letters and digits ("Thomas H. Kean Jr" ->
    "thomas h kean jr"), so the follow list and the stored names match however
    punctuation and capitals were typed."""
    return _KEY_STRIP_RE.sub(" ", (name or "").lower()).strip()


@dataclass(frozen=True)
class CongressTrade:
    """One row of a trade report, as known at the cutoff the caller asked about."""

    symbol: str | None
    member: str
    member_key: str
    state_district: str | None
    owner: str  # "self" | "spouse" | "dependent child" | "joint"
    owner_code: str
    asset: str
    asset_type: str | None
    ticker: str | None
    type: str  # "purchase" | "sale" | "partial sale" | "exchange"
    side: str  # "buy" | "sell" | "other"
    amount_low: int | None
    amount_high: int | None  # None for an open-ended top range
    amount_text: str
    trade_date: date | None
    notification_date: date | None
    filed_date: date | None
    known_at: datetime  # end of the filing day in New York (naive UTC), or the fetch time if sooner
    doc_id: str
    row_index: int
    filing_url: str | None
    notes: str
    payload: dict = field(compare=False, repr=False, default_factory=dict)

    @property
    def midpoint(self) -> float | None:
        """Middle of the range; None when it has no upper end. A label for display
        only: the true amount can be anywhere inside the range."""
        if self.amount_low is None or self.amount_high is None:
            return None
        return (self.amount_low + self.amount_high) / 2

    @property
    def filing_delay_days(self) -> int | None:
        """Calendar days between the trade and its report."""
        if self.trade_date is None or self.filed_date is None:
            return None
        return (self.filed_date - self.trade_date).days


@dataclass(frozen=True)
class CongressCluster:
    symbol: str
    start_date: date
    end_date: date
    member_count: int
    trade_count: int
    members: tuple[str, ...]
    total_low: int  # sum of the ranges' low ends
    total_high: int | None  # sum of the high ends; None when any range is open-ended
    first_known_at: datetime
    visible_from: datetime  # when the report that made it a cluster was filed


@dataclass(frozen=True)
class MemberSummary:
    member: str
    member_key: str
    state_district: str | None
    trade_count: int
    buy_count: int
    sell_count: int
    stock_buy_count: int
    filings: int
    newest_filing: date | None
    median_filing_delay_days: int | None


@dataclass(frozen=True)
class CongressFilingRecord:
    """One report, whether or not its table could be read."""

    doc_id: str
    member: str
    state_district: str | None
    filed_date: date | None
    status: str  # "ok" | "partial" | "unreadable"
    reason: str | None
    rows: int
    filing_url: str | None
    known_at: datetime


@dataclass(frozen=True)
class NetBuyers:
    """Distinct members buying and selling a stock over a window."""

    symbol: str
    window_days: int
    buyers: int
    sellers: int

    @property
    def net(self) -> int:
        return self.buyers - self.sellers


def _iso_date(raw) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def trade_from_fact(fact: KnownFact) -> CongressTrade:
    p = fact.payload or {}
    member = p.get("member") or ""
    return CongressTrade(
        symbol=fact.symbol,
        member=member,
        member_key=p.get("member_key") or member_key(member),
        state_district=p.get("state_district"),
        owner=p.get("owner") or "self",
        owner_code=p.get("owner_code") or "",
        asset=p.get("asset") or "",
        asset_type=p.get("asset_type"),
        ticker=p.get("ticker"),
        type=p.get("type") or "",
        side=p.get("side") or "other",
        amount_low=p.get("amount_low"),
        amount_high=p.get("amount_high"),
        amount_text=p.get("amount_text") or "",
        trade_date=_iso_date(p.get("trade_date")),
        notification_date=_iso_date(p.get("notification_date")),
        filed_date=_iso_date(p.get("filed_date")),
        known_at=fact.known_at,
        doc_id=str(p.get("doc_id") or ""),
        row_index=int(p.get("row_index") or 0),
        filing_url=fact.source_ref or p.get("filing_url"),
        notes=p.get("notes") or "",
        payload=p,
    )


def _cutoff(as_of: datetime | None) -> datetime:
    return to_naive_utc(as_of) if as_of is not None else current_as_of()


def _member_keys(members) -> set[str] | None:
    if members is None:
        return None
    return {member_key(m) for m in members if member_key(m)}


def congress_trades_as_of(
    session: Session,
    symbol: str | None = None,
    as_of: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    members=None,
    *,
    side: str | None = None,
    asset_types: tuple[str, ...] | None = None,
) -> list[CongressTrade]:
    """Trade rows in reports FILED in the `window_days` before the cutoff, newest
    filing first.

    - `symbol=None` returns every row (including those with no ticker); a symbol
      returns that ticker's rows.
    - `members`: names to keep (matched ignoring case and punctuation). An empty
      collection keeps nobody; None keeps everybody.
    - `side`: "buy", "sell" or "other"; None keeps all.
    - `asset_types`: e.g. ("ST",); None keeps every asset type."""
    cutoff = _cutoff(as_of)
    facts = facts_known_as_of(
        session,
        FactKind.CONGRESS_TRADE,
        as_of=as_of,
        symbol=symbol,
        since=cutoff - timedelta(days=window_days),
    )
    keys = _member_keys(members)
    out = []
    for fact in facts:
        trade = trade_from_fact(fact)
        if keys is not None and trade.member_key not in keys:
            continue
        if side is not None and trade.side != side:
            continue
        if asset_types is not None and trade.asset_type not in asset_types:
            continue
        out.append(trade)
    out.sort(key=lambda t: (t.known_at, t.doc_id, t.row_index), reverse=True)
    return out


def net_buyers_as_of(
    session: Session,
    symbol: str,
    as_of: datetime | None = None,
    window_days: int = 45,
    members=None,
) -> NetBuyers:
    """How many different members bought and how many sold this stock in reports
    filed in the window. A member who both bought and sold counts on both sides."""
    trades = congress_trades_as_of(
        session, symbol, as_of, window_days, members, asset_types=EQUITY_ASSET_TYPES
    )
    buyers = {t.member_key for t in trades if t.side == "buy"}
    sellers = {t.member_key for t in trades if t.side == "sell"}
    return NetBuyers(symbol=symbol.strip().upper(), window_days=window_days, buyers=len(buyers), sellers=len(sellers))


def has_congress_data(session: Session, as_of: datetime | None = None) -> bool:
    """True when at least one report was known at the cutoff. False means the
    data was never loaded, which is different from "Congress was quiet"."""
    return bool(facts_known_as_of(session, CONGRESS_FILING_KIND, as_of=as_of, limit=1))


def congress_clusters_as_of(
    session: Session,
    symbol: str | None = None,
    as_of: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    cluster_days: int = CLUSTER_DAYS,
    min_members: int = CLUSTER_MIN_MEMBERS,
    members=None,
) -> list[CongressCluster]:
    """Groups of stock purchases by at least `min_members` different members
    within `cluster_days` of each other, newest cluster first.

    Built per stock, greedily from the oldest trade (the same method as the
    insider clusters): take the purchases dated within `cluster_days` of it; with
    enough distinct members they form a cluster and are consumed, otherwise the
    oldest is dropped and the scan moves on. Trades are ordered by trade date (a
    row with no date uses its filing date). A cluster's `visible_from` is the
    filing time at which `min_members` members' reports had all been published,
    the earliest a live trader could have seen the cluster."""
    trades = congress_trades_as_of(
        session, symbol, as_of, window_days, members, side="buy", asset_types=EQUITY_ASSET_TYPES
    )
    by_symbol: dict[str, list[CongressTrade]] = {}
    for trade in trades:
        if trade.symbol:
            by_symbol.setdefault(trade.symbol, []).append(trade)

    clusters: list[CongressCluster] = []
    for sym, rows in by_symbol.items():
        dated = [(t.trade_date or t.filed_date or t.known_at.date(), t) for t in rows]
        dated.sort(key=lambda pair: (pair[0], pair[1].doc_id, pair[1].row_index))
        i = 0
        while i < len(dated):
            start = dated[i][0]
            group = [(d, t) for d, t in dated[i:] if (d - start).days <= cluster_days]
            people = {t.member_key for _, t in group}
            if len(people) >= min_members:
                group_trades = [t for _, t in group]
                lows = [t.amount_low or 0 for t in group_trades]
                highs = [t.amount_high for t in group_trades]
                clusters.append(
                    CongressCluster(
                        symbol=sym,
                        start_date=group[0][0],
                        end_date=group[-1][0],
                        member_count=len(people),
                        trade_count=len(group_trades),
                        members=tuple(sorted({t.member for t in group_trades})),
                        total_low=sum(lows),
                        total_high=None if any(h is None for h in highs) else sum(h for h in highs if h is not None),
                        first_known_at=min(t.known_at for t in group_trades),
                        visible_from=_visible_from(group_trades, min_members),
                    )
                )
                i += len(group)
            else:
                i += 1
    clusters.sort(key=lambda c: (c.end_date, c.symbol), reverse=True)
    return clusters


def _visible_from(group: list[CongressTrade], min_members: int) -> datetime:
    seen: set[str] = set()
    for trade in sorted(group, key=lambda t: t.known_at):
        seen.add(trade.member_key)
        if len(seen) >= min_members:
            return trade.known_at
    return max(t.known_at for t in group)


def congress_filings_as_of(
    session: Session,
    as_of: datetime | None = None,
    window_days: int | None = None,
    *,
    only_problems: bool = False,
) -> list[CongressFilingRecord]:
    """Reports known at the cutoff (newest first), optionally only those that
    could not be read in full."""
    cutoff = _cutoff(as_of)
    since = cutoff - timedelta(days=window_days) if window_days is not None else None
    out = []
    for fact in facts_known_as_of(session, CONGRESS_FILING_KIND, as_of=as_of, since=since):
        p = fact.payload or {}
        status = p.get("status") or "ok"
        if only_problems and status == "ok":
            continue
        out.append(
            CongressFilingRecord(
                doc_id=str(p.get("doc_id") or ""),
                member=p.get("member") or "",
                state_district=p.get("state_district"),
                filed_date=_iso_date(p.get("filed_date")),
                status=status,
                reason=p.get("reason"),
                rows=int(p.get("rows") or 0),
                filing_url=fact.source_ref or p.get("filing_url"),
                known_at=fact.known_at,
            )
        )
    return out


def congress_member_summaries_as_of(
    session: Session,
    as_of: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    members=None,
) -> list[MemberSummary]:
    """One line per member who filed in the window: how many trades, buys, sells,
    stock buys, reports, the newest filing and the typical delay between trade
    and report. Busiest first."""
    trades = congress_trades_as_of(session, None, as_of, window_days, members)
    groups: dict[str, list[CongressTrade]] = {}
    for trade in trades:
        groups.setdefault(trade.member_key, []).append(trade)
    out = []
    for key, rows in groups.items():
        delays = [d for d in (t.filing_delay_days for t in rows) if d is not None]
        filed = [t.filed_date for t in rows if t.filed_date]
        out.append(
            MemberSummary(
                member=rows[0].member,
                member_key=key,
                state_district=rows[0].state_district,
                trade_count=len(rows),
                buy_count=sum(1 for t in rows if t.side == "buy"),
                sell_count=sum(1 for t in rows if t.side == "sell"),
                stock_buy_count=sum(1 for t in rows if t.side == "buy" and t.asset_type in EQUITY_ASSET_TYPES),
                filings=len({t.doc_id for t in rows}),
                newest_filing=max(filed) if filed else None,
                median_filing_delay_days=int(median(delays)) if delays else None,
            )
        )
    out.sort(key=lambda s: (-s.trade_count, s.member))
    return out


def known_members_as_of(session: Session, as_of: datetime | None = None) -> list[tuple[str, str | None]]:
    """Every (member name, state/district) seen in a stored report, for the
    "who to follow" search. Alphabetical by name."""
    seen: dict[str, tuple[str, str | None]] = {}
    for fact in facts_known_as_of(session, CONGRESS_FILING_KIND, as_of=as_of):
        p = fact.payload or {}
        name = p.get("member")
        if name:
            seen.setdefault(member_key(name), (name, p.get("state_district")))
    return sorted(seen.values(), key=lambda pair: pair[0].lower())
