"""ui_check hook: synthetic House trade reports for the Smart Money Congress tab.

    backend/.venv/Scripts/python scripts/ui_check.py --be-port 8139 --fe-port 5239 \
        --routes /smart-money /settings --seed scripts/ui_seeds/congress.py --out <dir>

Writes made-up report rows straight into the throwaway database (two members buying
the same stock, a spouse's trade, an open-ended range, a sale, one scanned report),
then, once the sweep is done, clicks the Congress tab and screenshots it, and the
"who to follow" card on Settings. Nothing touches the network.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _url(doc: str, filed) -> str:
    return f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{filed.year}/{doc}.pdf"


def _row(session, record_fact, FactKind, doc, idx, member, district, symbol, side, owner, low, high, traded_ago, filed_ago, text=None):
    now = _now()
    traded = (now - timedelta(days=traded_ago)).date()
    filed = (now - timedelta(days=filed_ago)).date()
    record_fact(
        session,
        kind=FactKind.CONGRESS_TRADE,
        symbol=symbol,
        source="house_clerk",
        source_ref=_url(doc, filed),
        dedupe_key=f"house|{doc}|{idx}",
        known_at=now - timedelta(days=filed_ago, hours=2),
        effective_at=datetime.combine(traded, datetime.min.time()),
        payload={
            "doc_id": doc, "row_index": idx, "member": member, "member_key": member.lower().replace(".", ""),
            "state_district": district, "owner": owner, "owner_code": "SP" if owner == "spouse" else "",
            "asset": f"{symbol} Common Stock ({symbol})", "asset_type": "ST", "ticker": symbol,
            "type": "purchase" if side == "buy" else "sale", "side": side,
            "amount_low": low, "amount_high": high, "amount_text": text or f"${low:,} - ${high:,}",
            "trade_date": traded.isoformat(), "notification_date": filed.isoformat(), "filed_date": filed.isoformat(),
            "filing_url": _url(doc, filed), "notes": "",
        },
    )


def _filing(session, record_fact, doc, member, district, filed_ago, status="ok", rows=0, reason=None):
    from app.knowledge.congress_trades import CONGRESS_FILING_KIND

    now = _now()
    filed = (now - timedelta(days=filed_ago)).date()
    record_fact(
        session,
        kind=CONGRESS_FILING_KIND,
        symbol=None,
        source="house_clerk",
        source_ref=_url(doc, filed),
        dedupe_key=f"house|{doc}",
        known_at=now - timedelta(days=filed_ago, hours=2),
        effective_at=datetime.combine(filed, datetime.min.time()),
        payload={
            "doc_id": doc, "member": member, "state_district": district, "filed_date": filed.isoformat(),
            "status": status, "rows": rows, "reason": reason, "filing_url": _url(doc, filed),
        },
    )


def seed(ctx) -> None:
    sys.path.insert(0, str(BACKEND))
    from sqlmodel import Session, create_engine

    from app.knowledge import FactKind, record_fact

    engine = create_engine(f"sqlite:///{ctx.db_path}")
    with Session(engine) as s:
        r = lambda *a, **k: _row(s, record_fact, FactKind, *a, **k)  # noqa: E731
        r("9000001", 0, "Alex Example", "TX01", "NVDA", "buy", "self", 1_001, 15_000, 30, 12)
        r("9000002", 0, "Dana Sample", "CA11", "NVDA", "buy", "spouse", 250_001, 500_000, 25, 8)
        r("9000002", 1, "Dana Sample", "CA11", "AAPL", "sell", "spouse", 1_000_001, 5_000_000, 20, 8)
        r("9000003", 0, "Chris Placeholder", "NJ07", "MSFT", "buy", "self", 15_001, 50_000, 40, 3)
        r("9000003", 1, "Chris Placeholder", "NJ07", "AAPL", "buy", "self", 50_000_001, None, 40, 3, text="Over $50,000,000")
        s.commit()
        _filing(s, record_fact, "9000001", "Alex Example", "TX01", 12, rows=1)
        _filing(s, record_fact, "9000002", "Dana Sample", "CA11", 8, rows=2)
        _filing(s, record_fact, "9000003", "Chris Placeholder", "NJ07", 3, rows=2)
        _filing(s, record_fact, "9000004", "Pat Scanned", "FL02", 5, status="unreadable", reason="scanned image, no text to read")
        s.commit()
    engine.dispose()
    ctx.log("seeded 5 synthetic Congress rows and 4 reports (one unreadable)")


def interact(page, ctx) -> None:
    page.goto(f"{ctx.fe_url}/smart-money")
    page.get_by_role("button", name="Congress").click()
    page.get_by_text("Cluster buys").first.wait_for(timeout=15000)
    page.wait_for_timeout(800)
    ctx.screenshot(page, "congress-tab")
    res = ctx.api(
        "PUT", "/api/settings", json={"smart_money_follow_congress": "list", "smart_money_followed_members": ["Dana Sample"]}
    )
    ctx.log(f"saved follow list: HTTP {res.status_code}")
    page.goto(f"{ctx.fe_url}/settings")
    page.get_by_test_id("congress-follow").wait_for(timeout=15000)
    page.wait_for_timeout(500)
    ctx.screenshot(page, "congress-follow-card")
