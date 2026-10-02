"""The Smart Money "Funds" and "5% owners" sections: what the followed funds hold
(13F) and who has crossed 5% of a company (13D/13G), as the stored dated filings say.

Everything is read through `app.knowledge.fund_holdings`, which only ever shows a
filing from the moment SEC accepted it. Nothing here is scored or decides a
trade. The screens that use this repeat the limits of the data:

  * a 13F lists LONG positions only (no shorts, and hedges are invisible);
  * it is a snapshot of the last day of a quarter, filed up to 45 days later;
  * there are no trade dates, so "added" means "more shares at this quarter end
    than at the last one", and a stock split looks like a large "added";
  * a 13D/13G about a mega-cap is nearly always an index manager's routine
    filing: no individual owns 5% of Apple.

Loading filings (`refresh_funds`) is the only part that writes, and only the
refresh endpoint (and the backfill script) call it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import Session

from app.config import SEC_EDGAR_PLACEHOLDER_USER_AGENT, AppSettings, get_infra_settings
from app.data_providers import sec_13dg, sec_13f
from app.data_providers.base import DataProviderError
from app.data_providers.sec_13f import STARTER_FUNDS, followed_fund_ciks, starter_fund_name
from app.data_providers.sec_form4 import resolve_cik
from app.knowledge import FactKind, facts_known_as_of
from app.knowledge.fund_holdings import (
    STATUS_ADDED,
    STATUS_NEW,
    STATUS_SOLD_OUT,
    STATUS_TRIMMED,
    STATUS_UNCHANGED,
    FundFiling,
    FundHolder,
    OwnershipRecord,
    fund_changes_as_of,
    fund_filings_as_of,
    fund_holders_of_symbol,
    fund_positions_as_of,
    ownership_filings_as_of,
)
from app.schemas.funds_schemas import (
    FundChangeOut,
    FundChangesResponse,
    FundHolderOut,
    FundHoldersResponse,
    FundOut,
    FundPositionOut,
    FundsOverviewResponse,
    FundsRefreshResponse,
    OwnershipFilingOut,
    OwnershipResponse,
)

DEFAULT_OWNERSHIP_DAYS = 90
MAX_OWNERSHIP_DAYS = 365
MAX_CHANGE_ROWS = 200
TOP_HOLDINGS_ROWS = 15
MAX_OWNERSHIP_ROWS = 300

STATUS_FILTERS = ("all", STATUS_NEW, STATUS_ADDED, STATUS_TRIMMED, STATUS_SOLD_OUT, STATUS_UNCHANGED)
SCHEDULE_FILTERS = ("all", "13D", "13G")

# A refresh runs inside one web request. A fund with nothing stored gets its
# latest two quarters (enough to show what changed); a fund already loaded only
# looks for new filings. Ownership filings are looked up for at most this many
# watchlist companies per click, besides every followed fund's own filings.
REFRESH_NEW_FUND_QUARTERS = 2
REFRESH_OWNERSHIP_SYMBOLS = 15
REFRESH_OWNERSHIP_LOOKBACK_DAYS = 90

# How long the page asks a user to wait between refreshes (the router enforces it).
FUNDS_REFRESH_COOLDOWN_SECONDS = 60
INGEST_COMMAND = "python scripts/backfill_13f.py --quarters 4"


def _clean_cik(raw: str | int) -> str:
    return str(raw).strip().lstrip("0")


def followed_ciks(settings: AppSettings) -> list[str]:
    return [str(c) for c in followed_fund_ciks(settings.smart_money_followed_funds)]


def _fund_out(cik: str, filings: list[FundFiling]) -> FundOut:
    holdings = [f for f in filings if not f.is_notice and f.period is not None]
    periods = {f.period for f in holdings}
    newest = max(holdings, key=lambda f: (f.period, f.known_at)) if holdings else None
    latest_any = max(filings, key=lambda f: f.known_at) if filings else None
    name = (newest.manager if newest else None) or (latest_any.manager if latest_any else None) or starter_fund_name(cik) or f"CIK {cik}"
    return FundOut(
        cik=cik,
        name=name,
        is_starter=starter_fund_name(cik) is not None,
        has_data=newest is not None,
        latest_period=newest.period.isoformat() if newest and newest.period else None,
        latest_filed_at=newest.known_at if newest else None,
        latest_form=newest.form if newest else None,
        quarters_stored=len(periods),
        holdings_count=newest.holdings_count if newest else 0,
        matched_count=newest.matched_count if newest else 0,
        total_value=newest.total_value if newest else 0.0,
        value_unit=newest.value_unit if newest else None,
        latest_is_notice=bool(latest_any and latest_any.is_notice and (newest is None or latest_any.known_at > newest.known_at)),
    )


def overview(session: Session, settings: AppSettings) -> FundsOverviewResponse:
    ciks = followed_ciks(settings)
    all_filings = fund_filings_as_of(session)
    by_cik: dict[str, list[FundFiling]] = {}
    for filing in all_filings:
        by_cik.setdefault(filing.cik, []).append(filing)
    funds = [_fund_out(cik, by_cik.get(cik, [])) for cik in ciks]
    return FundsOverviewResponse(
        funds=funds,
        using_starter_list=not [c for c in settings.smart_money_followed_funds if str(c).strip()],
        has_data=any(f.has_data for f in funds),
        sec_contact_is_placeholder=get_infra_settings().sec_edgar_user_agent == SEC_EDGAR_PLACEHOLDER_USER_AGENT,
        ingest_command=INGEST_COMMAND,
        refresh_cooldown_seconds=FUNDS_REFRESH_COOLDOWN_SECONDS,
    )


def fund_changes(session: Session, cik: str, *, status: str = "all") -> FundChangesResponse:
    if status not in STATUS_FILTERS:
        raise ValueError(f"status must be one of {STATUS_FILTERS}")
    cik = _clean_cik(cik)
    change_set = fund_changes_as_of(session, cik)
    if change_set is None:
        return FundChangesResponse(
            cik=cik,
            manager=starter_fund_name(cik),
            has_data=False,
            period=None,
            previous_period=None,
            filed_at=None,
            previous_filed_at=None,
            consecutive=False,
            has_comparison=False,
            holdings_count=0,
            total_value=0.0,
            counts={},
            status=status,
            total=0,
            shown=0,
            changes=[],
            top_holdings=[],
            filing_url=None,
        )
    latest, positions = fund_positions_as_of(session, cik)
    total = change_set.total_value
    counts = {s: change_set.count(s) for s in (STATUS_NEW, STATUS_ADDED, STATUS_TRIMMED, STATUS_SOLD_OUT, STATUS_UNCHANGED)}
    rows = [c for c in change_set.changes if status == "all" or c.status == status]
    # "Unchanged" is the bulk of a portfolio and the least interesting part: it
    # only shows when asked for.
    if status == "all":
        rows = [c for c in rows if c.status != STATUS_UNCHANGED]
    shown = rows[:MAX_CHANGE_ROWS]
    return FundChangesResponse(
        cik=cik,
        manager=change_set.manager or starter_fund_name(cik),
        has_data=True,
        period=change_set.period.isoformat() if change_set.period else None,
        previous_period=change_set.previous_period.isoformat() if change_set.previous_period else None,
        filed_at=change_set.filed_at,
        previous_filed_at=change_set.previous_filed_at,
        consecutive=change_set.consecutive,
        has_comparison=change_set.has_comparison,
        holdings_count=change_set.holdings_count,
        total_value=total,
        counts=counts,
        status=status,
        total=len(rows),
        shown=len(shown),
        changes=[FundChangeOut(**c.__dict__) for c in shown],
        top_holdings=[
            FundPositionOut(
                cusip=p.cusip,
                issuer=p.issuer,
                symbol=p.symbol,
                put_call=p.put_call,
                share_type=p.share_type,
                shares=p.shares,
                value=p.value,
                weight_pct=(p.value / total * 100.0) if total else 0.0,
            )
            for p in positions[:TOP_HOLDINGS_ROWS]
        ],
        filing_url=latest.url if latest else None,
    )


def _holder_out(h: FundHolder) -> FundHolderOut:
    return FundHolderOut(**{**h.__dict__, "period": h.period.isoformat() if h.period else None})


def holders_of(session: Session, settings: AppSettings, symbol: str) -> FundHoldersResponse:
    symbol = (symbol or "").strip().upper()
    ciks = followed_ciks(settings)
    holders = fund_holders_of_symbol(session, symbol, ciks=ciks) if symbol else []
    stored = {f.cik for f in fund_filings_as_of(session) if not f.is_notice}
    return FundHoldersResponse(
        symbol=symbol,
        funds_stored=sum(1 for c in ciks if c in stored),
        holders=[_holder_out(h) for h in holders],
    )


def _ownership_out(r: OwnershipRecord) -> OwnershipFilingOut:
    return OwnershipFilingOut(
        symbol=r.symbol,
        accession=r.accession,
        schedule=r.schedule,
        form=r.form,
        is_amendment=r.is_amendment,
        known_at=r.known_at,
        event_date=r.event_date.isoformat() if r.event_date else None,
        filer_name=r.filer_name,
        issuer_name=r.issuer_name,
        class_title=r.class_title,
        percent=r.percent,
        shares=r.shares,
        rule=r.rule,
        purpose=r.purpose,
        person_count=r.person_count,
        filing_url=r.filing_url,
    )


def ownership(
    session: Session, *, days: int = DEFAULT_OWNERSHIP_DAYS, schedule: str = "all", symbol: str | None = None
) -> OwnershipResponse:
    if schedule not in SCHEDULE_FILTERS:
        raise ValueError(f"schedule must be one of {SCHEDULE_FILTERS}")
    symbol = (symbol or "").strip().upper() or None
    everything = ownership_filings_as_of(session, symbol, window_days=days)
    records = [r for r in everything if schedule == "all" or r.schedule == schedule]
    shown = records[:MAX_OWNERSHIP_ROWS]
    return OwnershipResponse(
        days=days,
        schedule=schedule,
        symbol=symbol,
        total=len(records),
        shown=len(shown),
        count_13d=sum(1 for r in everything if r.schedule == "13D"),
        count_13g=sum(1 for r in everything if r.schedule == "13G"),
        filings=[_ownership_out(r) for r in shown],
        has_data=bool(facts_known_as_of(session, FactKind.OWNERSHIP_FILING, limit=1)),
        refresh_cooldown_seconds=FUNDS_REFRESH_COOLDOWN_SECONDS,
    )


# --------------------------------------------------------------------------
# Writing: the refresh button
# --------------------------------------------------------------------------


@dataclass
class _Totals:
    funds: int = 0
    filings: int = 0
    holdings: int = 0
    ownership: int = 0
    legacy: int = 0
    errors: list[str] = field(default_factory=list)


def refresh_funds(
    session: Session,
    settings: AppSettings,
    symbols: list[str] | None = None,
    *,
    client=None,
    matcher=None,
) -> FundsRefreshResponse:
    """Load 13F reports for the followed funds and 13D/13G filings (by those
    funds, and about up to REFRESH_OWNERSHIP_SYMBOLS watchlist companies).
    Idempotent: a filing already stored is skipped. A failing fund or company is
    reported and the rest carry on."""
    from app.data_providers.sec_client import get_sec_client
    from app.services.smart_money_service import default_refresh_symbols

    client = client or get_sec_client()
    totals = _Totals()
    stored = {f.cik for f in fund_filings_as_of(session) if not f.is_notice}
    for cik in followed_ciks(settings):
        totals.funds += 1
        try:
            if cik in stored:
                fetched = sec_13f.fetch_new_13f(session, int(cik), client=client, matcher=matcher)
                totals.filings += sum(1 for r in fetched.results if r.is_new_filing)
                totals.holdings += sum(r.created for r in fetched.results)
            else:
                report = sec_13f.backfill_13f(
                    session, [cik], REFRESH_NEW_FUND_QUARTERS, client=client, matcher=matcher
                )
                totals.filings += report.filings_ingested
                totals.holdings += report.holdings_created
                totals.errors += report.errors
        except DataProviderError as exc:
            session.rollback()
            totals.errors.append(f"CIK {cik}: {exc}")
        try:
            owned = sec_13dg.fetch_ownership_filings(
                session,
                int(cik),
                None,
                client=client,
                lookback_days=REFRESH_OWNERSHIP_LOOKBACK_DAYS,
                symbol_resolver=lambda issuer_cik: sec_13dg.ticker_for_cik(issuer_cik, client=client),
            )
            totals.ownership += len(owned.new_facts)
            totals.legacy += owned.legacy_skipped
        except DataProviderError as exc:
            session.rollback()
            totals.errors.append(f"filings by CIK {cik}: {exc}")

    wanted = symbols or default_refresh_symbols(settings.scan_universe_size)
    done = 0
    for raw in wanted:
        if done >= REFRESH_OWNERSHIP_SYMBOLS:
            break
        symbol = (raw or "").strip().upper()
        if not symbol or symbol.endswith("-USD"):
            continue
        done += 1
        try:
            cik_int = resolve_cik(symbol, client=client)
            if cik_int is None:
                continue
            owned = sec_13dg.fetch_ownership_filings(
                session, cik_int, symbol, client=client, lookback_days=REFRESH_OWNERSHIP_LOOKBACK_DAYS
            )
            totals.ownership += len(owned.new_facts)
            totals.legacy += owned.legacy_skipped
        except DataProviderError as exc:
            session.rollback()
            totals.errors.append(f"{symbol}: {exc}")
    return FundsRefreshResponse(
        funds_processed=totals.funds,
        filings_ingested=totals.filings,
        holdings_created=totals.holdings,
        ownership_filings_created=totals.ownership,
        legacy_skipped=totals.legacy,
        errors=totals.errors[:20],
    )


__all__ = [
    "STARTER_FUNDS",
    "fund_changes",
    "holders_of",
    "overview",
    "ownership",
    "refresh_funds",
]
