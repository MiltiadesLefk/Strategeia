"""The Smart Money "Congress" tab's reading side: what members of the House have
reported trading, as the stored (dated) reports say it.

Everything is read through `app.knowledge.congress_trades`, which only ever shows a
report from the day it was filed, so the page can never display a trade before it
was public. Nothing here is scored or decides a trade.

Rules the page keeps:
  - amounts are RANGES; the midpoint is offered only as a labelled aid, never as the amount;
  - the trade date and the filing date are both shown, with the delay between them,
    because a report can be up to 45 days late (and spouse trades are included);
  - a report that could not be read (a scanned image) is counted and listed, never guessed at;
  - a symbol or window with no stored reports is "nothing loaded", never "Congress was quiet";
  - the Senate is not covered (its site refuses scripted access).

Loading reports (`refresh_congress`) is the only part that writes, and only the
refresh endpoint calls it.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session

from app.config import AppSettings
from app.data_providers.house_disclosures import (
    SENATE_SOURCE_NOTE,
    HouseBackfillReport,
    backfill_house,
    index_years,
)
from app.knowledge import FactKind, fact_stats
from app.knowledge.congress_trades import (
    CONGRESS_FILING_KIND,
    CongressCluster,
    CongressTrade,
    congress_clusters_as_of,
    congress_filings_as_of,
    congress_member_summaries_as_of,
    congress_trades_as_of,
    known_members_as_of,
    member_key,
)
from app.schemas.congress_schemas import (
    CongressClusterOut,
    CongressClustersResponse,
    CongressMemberNameOut,
    CongressMemberNamesResponse,
    CongressMemberOut,
    CongressMembersResponse,
    CongressRefreshResponse,
    CongressStatusResponse,
    CongressTradeOut,
    CongressTradesResponse,
    CongressUnreadableOut,
)
from app.timeutil import utcnow_naive

DEFAULT_DAYS = 90
MAX_DAYS = 365
# The table is a view, not an export: past this the response says how many more matched.
MAX_TRADE_ROWS = 500
MAX_CLUSTER_ROWS = 100
MAX_MEMBER_ROWS = 200
MAX_PROBLEM_ROWS = 20
MAX_NAME_MATCHES = 25

SIDES = ("buys", "sells", "all")
_SIDE_VALUES = {"buys": "buy", "sells": "sell", "all": None}

# A refresh downloads the index (one request) and then one PDF per unseen report,
# all inside one web request at one request a second. At most this many reports per
# click, oldest first; the response says how many are left, and clicking again
# continues. A first load looks back this far.
REFRESH_LOOKBACK_DAYS = 60
REFRESH_MAX_FILINGS = 15

INGEST_COMMAND = "python scripts/backfill_house.py --year 2025 --since 2025-01-01"


def followed_members(settings: AppSettings) -> list[str] | None:
    """The member names the follow setting limits views and alerts to, or None
    for "everyone". An empty list in "list" mode follows nobody (not everybody)."""
    if settings.smart_money_follow_congress == "list":
        return list(settings.smart_money_followed_members)
    return None


def _clean(value: str | None) -> str | None:
    cleaned = " ".join((value or "").split())
    return cleaned or None


def _symbol(value: str | None) -> str | None:
    cleaned = (value or "").strip().upper()
    return cleaned or None


def _trade_out(t: CongressTrade) -> CongressTradeOut:
    return CongressTradeOut(
        symbol=t.symbol,
        member=t.member,
        state_district=t.state_district,
        owner=t.owner,
        asset=t.asset,
        asset_type=t.asset_type,
        ticker=t.ticker,
        type=t.type,
        side=t.side,
        amount_low=t.amount_low,
        amount_high=t.amount_high,
        amount_text=t.amount_text,
        range_midpoint=t.midpoint,
        trade_date=t.trade_date.isoformat() if t.trade_date else None,
        filed_date=t.filed_date.isoformat() if t.filed_date else None,
        filing_delay_days=t.filing_delay_days,
        known_at=t.known_at,
        filing_url=t.filing_url,
        notes=t.notes,
    )


def list_trades(
    session: Session,
    settings: AppSettings,
    *,
    days: int = DEFAULT_DAYS,
    symbol: str | None = None,
    side: str = "all",
    member: str | None = None,
    followed_only: bool = False,
) -> CongressTradesResponse:
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    symbol = _symbol(symbol)
    member = _clean(member)
    followed = followed_members(settings)
    apply_follow = followed_only and followed is not None
    trades = congress_trades_as_of(
        session,
        symbol,
        window_days=days,
        members=followed if apply_follow else None,
        side=_SIDE_VALUES[side],
    )
    if member:
        wanted = member_key(member)
        trades = [t for t in trades if wanted in t.member_key]
    shown = trades[:MAX_TRADE_ROWS]
    return CongressTradesResponse(
        days=days,
        side=side,
        symbol=symbol,
        member=member,
        follow_mode=settings.smart_money_follow_congress,
        followed_only=apply_follow,
        total=len(trades),
        shown=len(shown),
        buy_count=sum(1 for t in trades if t.side == "buy"),
        sell_count=sum(1 for t in trades if t.side == "sell"),
        stock_buy_count=sum(1 for t in trades if t.side == "buy" and t.asset_type == "ST"),
        members=len({t.member_key for t in trades}),
        trades=[_trade_out(t) for t in shown],
    )


def _cluster_out(c: CongressCluster) -> CongressClusterOut:
    return CongressClusterOut(
        symbol=c.symbol,
        start_date=c.start_date.isoformat(),
        end_date=c.end_date.isoformat(),
        member_count=c.member_count,
        trade_count=c.trade_count,
        members=list(c.members),
        total_low=c.total_low,
        total_high=c.total_high,
        visible_from=c.visible_from,
    )


def list_clusters(
    session: Session,
    settings: AppSettings,
    *,
    days: int = DEFAULT_DAYS,
    symbol: str | None = None,
    followed_only: bool = False,
) -> CongressClustersResponse:
    followed = followed_members(settings)
    apply_follow = followed_only and followed is not None
    clusters = congress_clusters_as_of(
        session, _symbol(symbol), window_days=days, members=followed if apply_follow else None
    )
    return CongressClustersResponse(
        days=days,
        follow_mode=settings.smart_money_follow_congress,
        followed_only=apply_follow,
        clusters=[_cluster_out(c) for c in clusters[:MAX_CLUSTER_ROWS]],
    )


def list_members(session: Session, settings: AppSettings, *, days: int = DEFAULT_DAYS) -> CongressMembersResponse:
    followed = followed_members(settings)
    followed_keys = {member_key(m) for m in followed} if followed is not None else None
    summaries = congress_member_summaries_as_of(session, window_days=days)
    return CongressMembersResponse(
        days=days,
        follow_mode=settings.smart_money_follow_congress,
        members=[
            CongressMemberOut(
                member=s.member,
                state_district=s.state_district,
                trade_count=s.trade_count,
                buy_count=s.buy_count,
                sell_count=s.sell_count,
                stock_buy_count=s.stock_buy_count,
                filings=s.filings,
                newest_filing=s.newest_filing.isoformat() if s.newest_filing else None,
                median_filing_delay_days=s.median_filing_delay_days,
                followed=True if followed_keys is None else s.member_key in followed_keys,
            )
            for s in summaries[:MAX_MEMBER_ROWS]
        ],
    )


def member_names(session: Session, settings: AppSettings, query: str = "") -> CongressMemberNamesResponse:
    """Names seen in stored reports whose text contains `query` (the follow card's
    search). Only names already in the stored reports can be found here; any name
    can still be typed in by hand."""
    wanted = member_key(query)
    followed_keys = {member_key(m) for m in settings.smart_money_followed_members}
    matches = [
        (name, district)
        for name, district in known_members_as_of(session)
        if not wanted or wanted in member_key(name)
    ]
    return CongressMemberNamesResponse(
        total=len(matches),
        names=[
            CongressMemberNameOut(name=name, state_district=district, followed=member_key(name) in followed_keys)
            for name, district in matches[:MAX_NAME_MATCHES]
        ],
    )


def status(session: Session, settings: AppSettings) -> CongressStatusResponse:
    trades = fact_stats(session, FactKind.CONGRESS_TRADE)
    filings = fact_stats(session, CONGRESS_FILING_KIND)
    problems = congress_filings_as_of(session, only_problems=True)
    return CongressStatusResponse(
        has_data=filings.count > 0,
        trade_rows=trades.count,
        filings=filings.count,
        members=len(known_members_as_of(session)) if filings.count else 0,
        unreadable_filings=sum(1 for p in problems if p.status == "unreadable"),
        partial_filings=sum(1 for p in problems if p.status == "partial"),
        problem_filings=[
            CongressUnreadableOut(
                doc_id=p.doc_id,
                member=p.member,
                filed_date=p.filed_date.isoformat() if p.filed_date else None,
                status=p.status,
                reason=p.reason,
                rows=p.rows,
                filing_url=p.filing_url,
            )
            for p in problems[:MAX_PROBLEM_ROWS]
        ],
        oldest_filing=filings.first_known_at,
        newest_filing=filings.last_known_at,
        last_stored_at=filings.last_fetched_at,
        follow_mode=settings.smart_money_follow_congress,
        followed_members=list(settings.smart_money_followed_members),
        senate_note=SENATE_SOURCE_NOTE,
        ingest_command=INGEST_COMMAND,
    )


# --------------------------------------------------------------------------
# Writing: the refresh button
# --------------------------------------------------------------------------


def refresh_congress(session: Session, *, client=None, today: date | None = None) -> CongressRefreshResponse:
    """Load the newest unseen trade reports (idempotent: stored reports are
    skipped). At most REFRESH_MAX_FILINGS per call, oldest first."""
    today = today or utcnow_naive().date()
    budget = REFRESH_MAX_FILINGS
    total = HouseBackfillReport()
    year_used = today.year
    for year in index_years(today):
        part = backfill_house(
            session, year, since=today - timedelta(days=REFRESH_LOOKBACK_DAYS), max_filings=budget, client=client
        )
        total.filings_listed += part.filings_listed
        total.filings_ingested += part.filings_ingested
        total.filings_remaining += part.filings_remaining
        total.rows_created += part.rows_created
        total.unreadable += part.unreadable
        total.partial += part.partial
        total.errors.extend(part.errors)
        budget = max(0, budget - part.filings_ingested - len(part.errors))
        year_used = year
        if budget == 0:
            break
    return CongressRefreshResponse(
        year=year_used,
        filings_listed=total.filings_listed,
        filings_ingested=total.filings_ingested,
        filings_remaining=total.filings_remaining,
        rows_created=total.rows_created,
        unreadable=total.unreadable,
        partial=total.partial,
        errors=total.errors[:20],
    )
