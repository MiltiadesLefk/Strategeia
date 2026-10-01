"""The Smart Money section's reading side: what insiders have been trading, as
the stored (dated) filings say it.

Everything is read through `app.knowledge.insider_trades`, which only ever shows
a filing from the moment SEC accepted it, so the page can never display a trade
before it was public. Nothing here is scored or decides a trade: it shows the
same numbers the scorer reads and says plainly what the scorer would do with them.

Three rules from the evidence the scorer is built on:
  - only open-market buys (SEC code P) can ever add a point, and only one;
  - open-market sells (code S) are shown for context and never scored, because
    executives sell for pay, tax and pre-arranged plans far more often than for a view;
  - a symbol with no stored filings is "nothing loaded", never "insiders were quiet".

Loading filings (`refresh_insiders`) is the only part that writes, and only the
refresh endpoint calls it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from sqlmodel import Session

from app.analysis.insider_scoring import score_insider_activity
from app.config import SEC_EDGAR_PLACEHOLDER_USER_AGENT, get_infra_settings
from app.data_providers import universe
from app.data_providers.sec_form4 import BackfillReport, backfill_insider_trades
from app.knowledge import FactKind, current_as_of, fact_stats, facts_known_as_of
from app.knowledge.insider_trades import (
    OPEN_MARKET_BUY,
    OPEN_MARKET_SELL,
    InsiderCluster,
    InsiderTrade,
    insider_activity_as_of,
    insider_clusters_as_of,
    insider_trades_as_of,
)
from app.schemas.smart_money_schemas import (
    InsiderClusterOut,
    InsiderClustersResponse,
    InsiderRefreshResponse,
    InsiderStatusResponse,
    InsiderSummaryResponse,
    InsiderTradeOut,
    InsiderTradesResponse,
)
from app.timeutil import utcnow_naive

DEFAULT_DAYS = 90
MAX_DAYS = 365
# The table is a view, not an export: past this the response says how many more matched.
MAX_TRADE_ROWS = 500
MAX_CLUSTER_ROWS = 100

SIDES = ("buys", "sells", "all")
# "all" is both open-market codes: grants, option exercises and tax withholding
# are not market decisions, so they are left out of every view.
_SIDE_CODES = {
    "buys": (OPEN_MARKET_BUY,),
    "sells": (OPEN_MARKET_SELL,),
    "all": (OPEN_MARKET_BUY, OPEN_MARKET_SELL),
}

# A refresh asks SEC for each symbol's filing list and then for every unseen
# filing, all inside one web request. A symbol with no history yet gets a full
# quarter (the page's default window); a symbol already stored only needs the
# recent weeks. At most this many symbols are handled per click, the ones with
# nothing stored first; the response says how many are left.
REFRESH_NEW_SYMBOL_DAYS = 90
REFRESH_KNOWN_SYMBOL_DAYS = 14
REFRESH_MAX_SYMBOLS = 8

INGEST_COMMAND = "python scripts/backfill_insider_trades.py AAPL MSFT --since 2025-01-01"
# Wide enough to list every stored symbol on the status view.
_ALL_TIME_DAYS = 36500

_CEO_RE = re.compile(r"\b(ceo|chief executive)\b", re.IGNORECASE)
_CFO_RE = re.compile(r"\b(cfo|chief financial)\b", re.IGNORECASE)


def role_tags(roles: tuple[str, ...], officer_title: str | None) -> list[str]:
    """Badges for the table: CEO and CFO are called out from the officer title
    (the filing only says "officer"), then the plain roles."""
    tags: list[str] = []
    title = officer_title or ""
    if _CEO_RE.search(title):
        tags.append("CEO")
    if _CFO_RE.search(title):
        tags.append("CFO")
    if "officer" in roles and not tags:
        tags.append("Officer")
    if "director" in roles:
        tags.append("Director")
    if "10% owner" in roles:
        tags.append("10% owner")
    if not tags and roles:
        tags.append("Other")
    return tags


def _side(code: str | None) -> str:
    return "buy" if code == OPEN_MARKET_BUY else "sell" if code == OPEN_MARKET_SELL else "other"


def _filed_after_days(trade: InsiderTrade) -> int | None:
    if trade.transaction_date is None:
        return None
    return (trade.known_at.date() - trade.transaction_date).days


def _trade_out(t: InsiderTrade) -> InsiderTradeOut:
    return InsiderTradeOut(
        symbol=t.symbol,
        insider=t.owner_name,
        role_tags=role_tags(t.roles, t.officer_title),
        officer_title=t.officer_title,
        code=t.code,
        side=_side(t.code),
        shares=t.shares,
        price=t.price,
        value=t.value,
        transaction_date=t.transaction_date.isoformat() if t.transaction_date else None,
        known_at=t.known_at,
        filed_after_days=_filed_after_days(t),
        is_10b5_1=t.is_10b5_1,
        filing_url=t.filing_url,
    )


def _clean_symbol(symbol: str | None) -> str | None:
    cleaned = (symbol or "").strip().upper()
    return cleaned or None


def _symbols_with_filings(session: Session, days: int, symbol: str | None) -> list[str]:
    """Symbols with at least one filing accepted in the window, found through the
    same look-ahead-guarded reader as everything else."""
    if symbol:
        return [symbol]
    facts = facts_known_as_of(session, FactKind.INSIDER_TRADE, since=current_as_of() - timedelta(days=days))
    return sorted({f.symbol for f in facts if f.symbol})


def list_trades(
    session: Session, *, days: int = DEFAULT_DAYS, symbol: str | None = None, min_value: float = 0.0, side: str = "all"
) -> InsiderTradesResponse:
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    symbol = _clean_symbol(symbol)
    trades: list[InsiderTrade] = []
    for sym in _symbols_with_filings(session, days, symbol):
        trades.extend(insider_trades_as_of(session, sym, window_days=days, codes=_SIDE_CODES[side]))
    # A row with no price has no value to compare, so a minimum value hides it.
    trades = [t for t in trades if min_value <= 0 or (t.value is not None and t.value >= min_value)]
    trades.sort(key=lambda t: (t.known_at, t.accession, t.row_index), reverse=True)
    buys = [t for t in trades if t.code == OPEN_MARKET_BUY]
    sells = [t for t in trades if t.code == OPEN_MARKET_SELL]
    shown = trades[:MAX_TRADE_ROWS]
    return InsiderTradesResponse(
        days=days,
        side=side,
        symbol=symbol,
        min_value=min_value,
        total=len(trades),
        shown=len(shown),
        buy_value=sum(t.value or 0.0 for t in buys),
        sell_value=sum(t.value or 0.0 for t in sells),
        buy_count=len(buys),
        sell_count=len(sells),
        trades=[_trade_out(t) for t in shown],
    )


def _cluster_out(c: InsiderCluster) -> InsiderClusterOut:
    return InsiderClusterOut(
        symbol=c.symbol,
        start_date=c.start_date.isoformat(),
        end_date=c.end_date.isoformat(),
        insider_count=c.insider_count,
        trade_count=c.trade_count,
        total_value=c.total_value,
        unpriced_trades=c.unpriced_trades,
        role_tags=role_tags(c.roles, None),
        insiders=list(c.insiders),
        any_10b5_1=c.any_10b5_1,
        visible_from=c.visible_from,
    )


def list_clusters(session: Session, *, days: int = DEFAULT_DAYS, symbol: str | None = None) -> InsiderClustersResponse:
    symbol = _clean_symbol(symbol)
    symbols = _symbols_with_filings(session, days, symbol)
    clusters: list[InsiderCluster] = []
    for sym in symbols:
        clusters.extend(insider_clusters_as_of(session, sym, window_days=days))
    clusters.sort(key=lambda c: (c.end_date, c.symbol), reverse=True)
    return InsiderClustersResponse(
        days=days,
        symbols_checked=len(symbols),
        clusters=[_cluster_out(c) for c in clusters[:MAX_CLUSTER_ROWS]],
    )


def symbol_summary(session: Session, symbol: str, *, days: int = DEFAULT_DAYS) -> InsiderSummaryResponse:
    """The numbers the scorer reads (`insider_activity_as_of`), plus what it would
    do with them. Information only: nothing here feeds a plan."""
    symbol = _clean_symbol(symbol) or ""
    activity = insider_activity_as_of(session, symbol, window_days=days)
    if activity is None:
        return InsiderSummaryResponse(
            symbol=symbol,
            window_days=days,
            data_loaded=False,
            buy_count=0,
            sell_count=0,
            buy_value=0.0,
            sell_value=0.0,
            net_value=0.0,
            cluster_count=0,
            newest_filing=None,
            would_score_long=0,
            would_score_short=0,
            score_reasons=[],
        )
    long_points, reasons = score_insider_activity("long", activity)
    short_points, _ = score_insider_activity("short", activity)
    newest = facts_known_as_of(session, FactKind.INSIDER_TRADE, symbol=symbol, limit=1)
    return InsiderSummaryResponse(
        symbol=symbol,
        window_days=days,
        data_loaded=True,
        buy_count=activity.buy_count,
        sell_count=activity.sell_count,
        buy_value=activity.buy_value,
        sell_value=activity.sell_value,
        net_value=activity.net_value,
        cluster_count=len(insider_clusters_as_of(session, symbol, window_days=days)),
        newest_filing=newest[0].known_at if newest else None,
        would_score_long=long_points,
        would_score_short=short_points,
        score_reasons=reasons,
    )


def status(session: Session) -> InsiderStatusResponse:
    stats = fact_stats(session, FactKind.INSIDER_TRADE)
    symbols = _symbols_with_filings(session, _ALL_TIME_DAYS, None) if stats.count else []
    return InsiderStatusResponse(
        has_data=stats.count > 0,
        trade_rows=stats.count,
        symbols=stats.symbols,
        oldest_filing=stats.first_known_at,
        newest_filing=stats.last_known_at,
        last_stored_at=stats.last_fetched_at,
        symbol_list=symbols,
        sec_contact_is_placeholder=get_infra_settings().sec_edgar_user_agent == SEC_EDGAR_PLACEHOLDER_USER_AGENT,
        ingest_command=INGEST_COMMAND,
    )


# --------------------------------------------------------------------------
# Writing: the refresh button
# --------------------------------------------------------------------------


def default_refresh_symbols(scan_size: int) -> list[str]:
    """The watchlist's scanned symbols, minus crypto (no Form 4 exists for it)."""
    return [e.symbol for e in universe.load_universe()[:scan_size] if not e.symbol.endswith("-USD")]


@dataclass
class _Plan:
    fresh: list[str]
    known: list[str]
    remaining: int


def _plan_refresh(session: Session, symbols: list[str]) -> _Plan:
    ordered: list[str] = []
    for raw in symbols:
        cleaned = _clean_symbol(raw)
        if cleaned and cleaned not in ordered:
            ordered.append(cleaned)
    stored = set(status(session).symbol_list)
    # Symbols with nothing stored come first: they are the ones the page is missing.
    ordered.sort(key=lambda s: s in stored)
    chosen = ordered[:REFRESH_MAX_SYMBOLS]
    return _Plan(
        fresh=[s for s in chosen if s not in stored],
        known=[s for s in chosen if s in stored],
        remaining=len(ordered) - len(chosen),
    )


def refresh_insiders(session: Session, symbols: list[str], *, client=None, today=None) -> InsiderRefreshResponse:
    """Load Form 4 filings for `symbols` (idempotent: filings already stored are
    skipped). Symbols are processed in batches of REFRESH_MAX_SYMBOLS."""
    plan = _plan_refresh(session, symbols)
    today = today or utcnow_naive().date()
    total = BackfillReport()
    for group, lookback in ((plan.fresh, REFRESH_NEW_SYMBOL_DAYS), (plan.known, REFRESH_KNOWN_SYMBOL_DAYS)):
        if not group:
            continue
        part = backfill_insider_trades(session, group, today - timedelta(days=lookback), today, client=client)
        total.filings_seen += part.filings_seen
        total.filings_ingested += part.filings_ingested
        total.facts_created += part.facts_created
        total.unknown_symbols += part.unknown_symbols
        total.errors += part.errors
    handled = len(plan.fresh) + len(plan.known)
    return InsiderRefreshResponse(
        symbols_requested=handled + plan.remaining,
        symbols_processed=handled,
        symbols_remaining=plan.remaining,
        filings_seen=total.filings_seen,
        filings_ingested=total.filings_ingested,
        rows_created=total.facts_created,
        unknown_symbols=total.unknown_symbols,
        errors=total.errors[:20],
    )
