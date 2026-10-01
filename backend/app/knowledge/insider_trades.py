"""Reading insider trades as they were known at a moment in time.

The facts are written by `app.data_providers.sec_form4` (one `insider_trade`
fact per Form 4 transaction row, `known_at` = the filing's SEC acceptance time,
`effective_at` = the transaction date). Everything here reads them back through
`facts_known_as_of`, so a row whose filing was accepted after the cutoff is
invisible even if the trade itself happened earlier. Inside `with as_of(t):` the
`as_of` argument can be left out and defaults to the simulated moment.

The window is measured on `known_at` (filings accepted in the last N days), the
same way the live provider counts filings from the last 90 days. A trade made
three weeks ago but only filed yesterday therefore counts as yesterday's news,
which is exactly when a trader could have reacted to it.

Amendments: a 4/A restates the original filing in full. From the moment the
amendment was accepted, it replaces the original's rows (matched on company,
insider, period of report and the original filing date); before that moment the
original is what was known and stays visible. If one insider filed several
original Forms 4 for the same period on the same day, an amendment cannot be
matched to one of them with certainty, so none is replaced and both sets stay
(it may double count rather than silently drop a real trade).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlmodel import Session

from app.data_providers.base import InsiderActivity
from app.knowledge import FactKind, KnownFact, current_as_of, facts_known_as_of, to_naive_utc

DEFAULT_WINDOW_DAYS = 90
# The same two codes the live provider counts: open-market purchases and sales.
OPEN_MARKET_BUY = "P"
OPEN_MARKET_SELL = "S"
# A cluster is several different insiders buying within this many days of each
# other: more telling than one person buying, because it is independent
# conviction rather than one person's habit.
CLUSTER_DAYS = 14
CLUSTER_MIN_INSIDERS = 2


@dataclass(frozen=True)
class InsiderTrade:
    """One transaction row, as known at the cutoff the caller asked about."""

    symbol: str
    accession: str
    row_index: int
    form: str
    known_at: datetime  # SEC acceptance time (naive UTC)
    transaction_date: date | None
    table: str  # "non_derivative" | "derivative"
    code: str | None
    acquired_disposed: str | None
    shares: float | None
    price: float | None
    value: float | None  # shares * price; None when the filing gave no price
    shares_after: float | None
    ownership: str | None
    owner_name: str | None
    owner_key: str | None
    roles: tuple[str, ...]
    officer_title: str | None
    is_10b5_1: bool
    filing_url: str | None
    payload: dict = field(compare=False, repr=False, default_factory=dict)


@dataclass(frozen=True)
class InsiderCluster:
    symbol: str
    start_date: date
    end_date: date
    insider_count: int
    trade_count: int
    total_shares: float
    total_value: float  # only rows that had a price; see `unpriced_trades`
    unpriced_trades: int
    roles: tuple[str, ...]  # distinct roles seen, sorted
    insiders: tuple[str, ...]
    any_10b5_1: bool
    first_known_at: datetime
    visible_from: datetime  # when the second distinct insider's filing was accepted


def _roles(payload: dict) -> tuple[str, ...]:
    out = []
    if payload.get("is_officer"):
        out.append("officer")
    if payload.get("is_director"):
        out.append("director")
    if payload.get("is_ten_percent_owner"):
        out.append("10% owner")
    if payload.get("is_other"):
        out.append("other")
    return tuple(out)


def _iso_date(raw) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _to_trade(fact: KnownFact) -> InsiderTrade:
    p = fact.payload or {}
    return InsiderTrade(
        symbol=fact.symbol or p.get("symbol") or "",
        accession=p.get("accession") or "",
        row_index=int(p.get("row_index") or 0),
        form=p.get("form") or "4",
        known_at=fact.known_at,
        transaction_date=_iso_date(p.get("transaction_date")),
        table=p.get("table") or "non_derivative",
        code=p.get("code"),
        acquired_disposed=p.get("acquired_disposed"),
        shares=p.get("shares"),
        price=p.get("price"),
        value=p.get("value"),
        shares_after=p.get("shares_after"),
        ownership=p.get("ownership"),
        owner_name=p.get("owner_name"),
        owner_key=p.get("owner_key"),
        roles=_roles(p),
        officer_title=p.get("officer_title"),
        is_10b5_1=bool(p.get("is_10b5_1")),
        filing_url=fact.source_ref or p.get("filing_url"),
        payload=p,
    )


def _group_key(p: dict) -> tuple:
    """What an amendment and the filing it corrects have in common."""
    original_date = p.get("date_of_original_submission") if p.get("is_amendment") else p.get("filing_date")
    return (p.get("issuer_cik"), p.get("owner_key"), p.get("period_of_report"), original_date)


def _apply_amendments(facts: list[KnownFact]) -> list[KnownFact]:
    """Drop original rows replaced by an amendment that is itself in `facts`
    (the caller has already limited `facts` to what was known at the cutoff)."""
    originals: dict[tuple, set[str]] = {}
    amendments: dict[tuple, dict[str, datetime]] = {}
    for fact in facts:
        p = fact.payload or {}
        accession = p.get("accession")
        if not accession:
            continue
        key = _group_key(p)
        if p.get("is_amendment"):
            amendments.setdefault(key, {})[accession] = fact.known_at
        else:
            originals.setdefault(key, set()).add(accession)

    replaced: set[str] = set()
    for key, amended in amendments.items():
        accessions = originals.get(key, set())
        if len(accessions) == 1:  # unambiguous
            replaced |= accessions
    # Several amendments of the same filing: the latest one is the full truth.
    superseded_amendments: set[str] = set()
    for amended in amendments.values():
        if len(amended) > 1:
            newest = max(amended, key=lambda a: (amended[a], a))
            superseded_amendments |= {a for a in amended if a != newest}
    dropped = replaced | superseded_amendments
    return [f for f in facts if (f.payload or {}).get("accession") not in dropped]


def _cutoff(as_of: datetime | None) -> datetime:
    return to_naive_utc(as_of) if as_of is not None else current_as_of()


def insider_trades_as_of(
    session: Session,
    symbol: str,
    as_of: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    codes: tuple[str, ...] | None = (OPEN_MARKET_BUY,),
    *,
    include_derivative: bool = False,
    include_10b5_1: bool = True,
) -> list[InsiderTrade]:
    """Transactions in filings accepted in the `window_days` before the cutoff,
    newest filing first. `codes=None` returns every code; the default is open
    market purchases only. Derivative-table rows (options, RSUs) are left out
    unless asked for: a grant or exercise there is not a market decision.
    `include_10b5_1=False` drops rows flagged as made under a pre-arranged
    trading plan."""
    cutoff = _cutoff(as_of)
    facts = facts_known_as_of(
        session,
        FactKind.INSIDER_TRADE,
        as_of=as_of,
        symbol=symbol,
        since=cutoff - timedelta(days=window_days),
    )
    trades = [_to_trade(f) for f in _apply_amendments(facts)]
    return [
        t
        for t in trades
        if (codes is None or t.code in codes)
        and (include_derivative or t.table == "non_derivative")
        and (include_10b5_1 or not t.is_10b5_1)
    ]


def insider_activity_as_of(
    session: Session,
    symbol: str,
    as_of: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> InsiderActivity | None:
    """The same summary the live provider returns (`InsiderActivity`): counts and
    dollar value of open-market purchases and sales over the window, here as it
    stood at the cutoff.

    Returns None when no insider fact for this symbol was known at the cutoff at
    all: the data was never loaded (or the company had no filings yet), which is
    different from "insiders were quiet" and must not be scored as zero evidence
    of anything. Differences from the live provider, on purpose: only the
    non-derivative table is counted (the live regex also counts option rows with
    a P or S code), a row with no price counts toward the count but adds no
    value (as live), and amended filings are counted once, as amended."""
    if not facts_known_as_of(session, FactKind.INSIDER_TRADE, as_of=as_of, symbol=symbol, limit=1):
        return None
    buys = insider_trades_as_of(session, symbol, as_of, window_days, codes=(OPEN_MARKET_BUY,))
    sells = insider_trades_as_of(session, symbol, as_of, window_days, codes=(OPEN_MARKET_SELL,))
    return InsiderActivity(
        symbol=symbol.strip().upper(),
        window_days=window_days,
        buy_count=len(buys),
        sell_count=len(sells),
        buy_value=sum(t.value or 0.0 for t in buys),
        sell_value=sum(t.value or 0.0 for t in sells),
    )


def insider_clusters_as_of(
    session: Session,
    symbol: str,
    as_of: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    cluster_days: int = CLUSTER_DAYS,
    min_insiders: int = CLUSTER_MIN_INSIDERS,
    *,
    include_10b5_1: bool = True,
) -> list[InsiderCluster]:
    """Groups of open-market purchases by at least `min_insiders` different
    people within `cluster_days` of each other, newest cluster first.

    Built greedily from the oldest trade: take all purchases dated within
    `cluster_days` of it; if they come from enough distinct insiders they form a
    cluster and are consumed, otherwise the oldest is dropped and the scan moves
    on. Trades are ordered by transaction date; undated rows are ignored. A
    cluster's `visible_from` is when the filing that completed it was accepted,
    the earliest a live trader could have seen the cluster."""
    trades = [
        t
        for t in insider_trades_as_of(
            session, symbol, as_of, window_days, codes=(OPEN_MARKET_BUY,), include_10b5_1=include_10b5_1
        )
        if t.transaction_date is not None
    ]
    trades.sort(key=lambda t: (t.transaction_date, t.accession, t.row_index))
    clusters: list[InsiderCluster] = []
    i = 0
    while i < len(trades):
        start = trades[i].transaction_date
        group = [t for t in trades[i:] if (t.transaction_date - start).days <= cluster_days]
        people = {t.owner_key or t.owner_name for t in group}
        if len(people) >= min_insiders:
            priced = [t for t in group if t.value is not None]
            clusters.append(
                InsiderCluster(
                    symbol=symbol.strip().upper(),
                    start_date=group[0].transaction_date,
                    end_date=group[-1].transaction_date,
                    insider_count=len(people),
                    trade_count=len(group),
                    total_shares=sum(t.shares or 0.0 for t in group),
                    total_value=sum(t.value for t in priced),
                    unpriced_trades=len(group) - len(priced),
                    roles=tuple(sorted({r for t in group for r in t.roles})),
                    insiders=tuple(sorted({t.owner_name or "" for t in group})),
                    any_10b5_1=any(t.is_10b5_1 for t in group),
                    first_known_at=min(t.known_at for t in group),
                    visible_from=_visible_from(group, min_insiders),
                )
            )
            i += len(group)
        else:
            i += 1
    clusters.sort(key=lambda c: c.end_date, reverse=True)
    return clusters


def _visible_from(group: list[InsiderTrade], min_insiders: int) -> datetime:
    """The acceptance time at which `min_insiders` distinct people's filings had
    been published, i.e. the earliest moment the cluster could be observed."""
    seen: set[str | None] = set()
    for trade in sorted(group, key=lambda t: t.known_at):
        seen.add(trade.owner_key or trade.owner_name)
        if len(seen) >= min_insiders:
            return trade.known_at
    return max(t.known_at for t in group)
