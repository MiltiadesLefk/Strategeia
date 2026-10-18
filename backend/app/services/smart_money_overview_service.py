"""One view over every smart-money source: totals per source for a window, and the stocks ranked by their combined score.

Read-only. The ranking is the morning scan's own scorer (`smartmoney.tracker.smart_money_score_as_of`, 30-day window),
so a stock's total here is the number the scan compares with `smart_money_min_score`; `days` only widens the totals.
The filters mirror the Insiders tab: side (buys / sells), a minimum value (insider trades by dollar value, Congress
trades by the low end of the reported range; funds and 5% owners have no such figure) and one symbol.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, func, select

from app.config import AppSettings
from app.knowledge import current_as_of
from app.knowledge.congress_trades import EQUITY_ASSET_TYPES, congress_clusters_as_of, congress_trades_as_of
from app.knowledge.fund_holdings import STATUS_ADDED, STATUS_NEW, STATUS_SOLD_OUT, STATUS_TRIMMED, ownership_filings_as_of
from app.services import smart_money_service
from app.services.congress_service import range_sums
from app.smartmoney.models import STATUS_NEEDS_REVIEW, SmartMoneyCandidate, STATUS_NEW as CANDIDATE_NEW
from app.smartmoney.scoring import WINDOW_DAYS
from app.smartmoney.tracker import fund_sets_at, smart_money_score_as_of

MAX_SCORED = 150  # stocks scored per request (the busiest sources first would need more reads than this is worth)
SOURCES = ("insider", "house", "fund", "ownership", "filing_8k")
BUY_MOVES = (STATUS_NEW, STATUS_ADDED)
SELL_MOVES = (STATUS_TRIMMED, STATUS_SOLD_OUT)


def ranked_stocks(session: Session, settings: AppSettings, now: datetime, symbol: str | None = None, fsets=None) -> list[dict]:
    """Every stock with smart-money evidence in the scorer's 30-day window, scored, best first. Zero-score stocks are left out."""
    fsets = fsets if fsets is not None else fund_sets_at(session, settings, now)
    sym_filter = (symbol or "").strip().upper() or None
    insiders = smart_money_service.list_trades(session, days=WINDOW_DAYS, side="buys", symbol=sym_filter)
    house = congress_trades_as_of(session, sym_filter, now, WINDOW_DAYS, asset_types=EQUITY_ASSET_TYPES)
    owners = ownership_filings_as_of(session, sym_filter, now, WINDOW_DAYS)
    moves = [
        c.symbol for _, cs in fsets.values() if (now - cs.filed_at).days <= WINDOW_DAYS for c in cs.changes
        if c.symbol and c.share_type == "SH" and c.put_call is None and c.status in BUY_MOVES
    ]
    pointed_at = (
        [t.symbol for t in insiders.trades]
        + [t.symbol for t in house if t.side == "buy" and t.symbol]
        + [o.symbol for o in owners if o.symbol]
        + moves
    )
    counts: dict[str, int] = {}  # symbol -> how many source events point at it (picks who is scored first)
    for sym in pointed_at:
        if not sym_filter or sym.upper() == sym_filter:
            counts[sym.upper()] = counts.get(sym.upper(), 0) + 1
    if sym_filter:
        counts.setdefault(sym_filter, 0)
    rows = []
    for sym in sorted(counts, key=lambda s: -counts[s])[:MAX_SCORED]:
        scored = smart_money_score_as_of(session, sym, None, settings, fund_sets=fsets)
        parts = {src: round(sum(c.points for c in scored.result.components if c.source == src), 1) for src in SOURCES}
        rows.append({"symbol": sym, "total": round(scored.result.total, 1), "parts": parts,
                     "reasons": [c.reason for c in scored.result.components][:4]})
    rows = [r for r in rows if r["total"] != 0]  # evidence that earns no points is not a ranking
    rows.sort(key=lambda r: -r["total"])
    return rows


def overview(
    session: Session, settings: AppSettings, days: int = 30, limit: int = 30, *,
    side: str = "all", min_value: float = 0.0, symbol: str | None = None,
) -> dict:
    now = current_as_of()
    sym_filter = (symbol or "").strip().upper() or None
    want_buys, want_sells = side in ("all", "buys"), side in ("all", "sells")

    insiders = smart_money_service.list_trades(session, days=days, side=side, symbol=sym_filter, min_value=min_value)
    house = [
        t for t in congress_trades_as_of(session, sym_filter, now, days, asset_types=EQUITY_ASSET_TYPES)
        if (t.side == "buy" and want_buys or t.side == "sell" and want_sells)
        and (min_value <= 0 or (t.amount_low or 0) >= min_value)
    ]
    owners = ownership_filings_as_of(session, sym_filter, now, days)
    fsets = fund_sets_at(session, settings, now)
    wanted = (BUY_MOVES if want_buys else ()) + (SELL_MOVES if want_sells else ())
    changes = [
        c for _, cs in fsets.values() if (now - cs.filed_at).days <= days for c in cs.changes
        if c.symbol and (not sym_filter or c.symbol.upper() == sym_filter) and c.share_type == "SH" and c.put_call is None
        and c.status in wanted
    ]
    moves = [c.status for c in changes]
    # Dollar change of the position: what a buy added to the fund's holding, what a sell took out of it.
    fund_buy_value = sum(c.value_now - c.value_before for c in changes if c.status in BUY_MOVES)
    fund_sell_value = sum(c.value_before - c.value_now for c in changes if c.status in SELL_MOVES)
    clusters = {
        "insider": len(smart_money_service.list_clusters(session, days=days, symbol=sym_filter).clusters) if want_buys else 0,
        "congress": len(congress_clusters_as_of(session, sym_filter, now, days)) if want_buys else 0,
    }

    ranked = ranked_stocks(session, settings, now, sym_filter, fsets)
    if side == "buys":
        ranked = [r for r in ranked if r["total"] > 0]
    elif side == "sells":
        ranked = sorted((r for r in ranked if r["total"] < 0), key=lambda r: r["total"])

    counts = dict(session.exec(select(SmartMoneyCandidate.status, func.count(SmartMoneyCandidate.id)).group_by(SmartMoneyCandidate.status)).all())
    return {
        "days": days,
        "side": side,
        "min_score": settings.smart_money_min_score,
        "totals": {
            "insider": {"buys": insiders.buy_count, "buy_value": insiders.buy_value, "sells": insiders.sell_count, "sell_value": insiders.sell_value},
            "congress": {"buys": sum(t.side == "buy" for t in house), "sells": sum(t.side == "sell" for t in house),
                         "members": len({t.member_key for t in house}), **range_sums(house)},
            "funds": {"followed_with_filings": len(fsets), "new_positions": moves.count(STATUS_NEW), "added": moves.count(STATUS_ADDED),
                      "trimmed": moves.count(STATUS_TRIMMED), "sold_out": moves.count(STATUS_SOLD_OUT),
                      "buy_value": fund_buy_value, "sell_value": fund_sell_value},
            "owners": {"13D": sum(o.schedule == "13D" for o in owners), "13G": sum(o.schedule == "13G" for o in owners)},
            "clusters": clusters,
            "candidates": {"new": counts.get(CANDIDATE_NEW, 0), "needs_review": counts.get(STATUS_NEEDS_REVIEW, 0), "all": sum(counts.values())},
        },
        "ranked": ranked[:limit],
        "scored_stocks": len(ranked),
        "passing": sum(r["total"] >= settings.smart_money_min_score for r in ranked),
    }
