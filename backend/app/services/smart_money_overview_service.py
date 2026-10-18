"""One view over every smart-money source: totals per source for a window, and the stocks ranked by their combined score.

Read-only. The ranking is the morning scan's own scorer (`smartmoney.tracker.smart_money_score_as_of`, 30-day window),
so a stock's total here is the number the scan compares with `smart_money_min_score`; `days` only widens the totals.
"""

from __future__ import annotations

from sqlmodel import Session, func, select

from app.config import AppSettings
from app.knowledge import current_as_of
from app.knowledge.congress_trades import EQUITY_ASSET_TYPES, congress_trades_as_of
from app.knowledge.fund_holdings import STATUS_ADDED, STATUS_NEW, ownership_filings_as_of
from app.services import smart_money_service
from app.smartmoney.models import STATUS_NEEDS_REVIEW, SmartMoneyCandidate, STATUS_NEW as CANDIDATE_NEW
from app.smartmoney.scoring import WINDOW_DAYS
from app.smartmoney.tracker import fund_sets_at, smart_money_score_as_of

MAX_SCORED = 150  # stocks scored per request (the busiest sources first would need more reads than this is worth)
SOURCES = ("insider", "house", "fund", "ownership", "filing_8k")


def overview(session: Session, settings: AppSettings, days: int = 30, limit: int = 30) -> dict:
    now = current_as_of()

    def events(window: int):
        insiders = smart_money_service.list_trades(session, days=window, side="all")
        house = [t for t in congress_trades_as_of(session, None, now, window, asset_types=EQUITY_ASSET_TYPES) if t.side in ("buy", "sell")]
        return insiders, house, ownership_filings_as_of(session, None, now, window)

    insiders, house, owners = events(days)
    fsets = fund_sets_at(session, settings, now)

    def moves(window: int) -> list[tuple[str, str]]:
        """New or added positions in 13Fs filed inside the window (an older filing no longer scores)."""
        return [
            (c.symbol, c.status) for _, cs in fsets.values() if (now - cs.filed_at).days <= window for c in cs.changes
            if c.symbol and c.share_type == "SH" and c.put_call is None and c.status in (STATUS_NEW, STATUS_ADDED)
        ]

    fund_moves, ranking_moves = moves(days), moves(WINDOW_DAYS)
    # Who gets scored comes from the scorer's own window, so the ranking never lists stocks whose evidence has aged out.
    r_ins, r_house, r_owners = (insiders, house, owners) if days == WINDOW_DAYS else events(WINDOW_DAYS)
    symbols: dict[str, int] = {}  # symbol -> how many source events point at it (to pick who gets scored first)
    pointed_at = (
        [t.symbol for t in r_ins.trades if t.side == "buy"]
        + [t.symbol for t in r_house if t.side == "buy" and t.symbol]
        + [o.symbol for o in r_owners if o.symbol]
        + [sym for sym, _ in ranking_moves]
    )
    for sym in pointed_at:
        symbols[sym.upper()] = symbols.get(sym.upper(), 0) + 1
    ranked = []
    for sym in sorted(symbols, key=lambda s: -symbols[s])[:MAX_SCORED]:
        scored = smart_money_score_as_of(session, sym, None, settings, fund_sets=fsets)
        parts = {src: round(sum(c.points for c in scored.result.components if c.source == src), 1) for src in SOURCES}
        ranked.append({"symbol": sym, "total": round(scored.result.total, 1), "parts": parts,
                       "reasons": [c.reason for c in scored.result.components][:4]})
    ranked.sort(key=lambda r: -r["total"])
    scored_count = len(ranked)
    ranked = [r for r in ranked if r["total"] != 0]  # evidence that earns no points is not a ranking

    counts = dict(session.exec(select(SmartMoneyCandidate.status, func.count(SmartMoneyCandidate.id)).group_by(SmartMoneyCandidate.status)).all())
    return {
        "days": days,
        "min_score": settings.smart_money_min_score,
        "totals": {
            "insider": {"buys": insiders.buy_count, "buy_value": insiders.buy_value, "sells": insiders.sell_count, "sell_value": insiders.sell_value},
            "congress": {"buys": sum(t.side == "buy" for t in house), "sells": sum(t.side == "sell" for t in house),
                         "members": len({t.member_key for t in house})},
            "funds": {"followed_with_filings": len(fsets), "new_positions": sum(s == STATUS_NEW for _, s in fund_moves),
                      "added": sum(s == STATUS_ADDED for _, s in fund_moves)},
            "owners": {"13D": sum(o.schedule == "13D" for o in owners), "13G": sum(o.schedule == "13G" for o in owners)},
            "candidates": {"new": counts.get(CANDIDATE_NEW, 0), "needs_review": counts.get(STATUS_NEEDS_REVIEW, 0), "all": sum(counts.values())},
        },
        "ranked": ranked[:limit],
        "scored_stocks": scored_count,
        "passing": sum(r["total"] >= settings.smart_money_min_score for r in ranked),
    }
