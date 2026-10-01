"""ui_check hook: synthetic insider filings for the Smart Money page.

    backend/.venv/Scripts/python scripts/ui_check.py --be-port 8131 --fe-port 5231 \
        --routes /smart-money "/analysis?symbol=AAPL" --seed scripts/ui_seeds/smart_money.py --out <dir>

Writes made-up Form 4 rows straight into the throwaway database (a two-insider cluster, a
10b5-1 sale, a lone buy, an unpriced buy). Nothing touches the network.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def _trade(session, record_fact, FactKind, symbol, accession, owner, days_ago_traded, days_ago_filed, **kw):
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    traded = (now - timedelta(days=days_ago_traded)).date()
    accepted = now - timedelta(days=days_ago_filed, hours=1)
    code = kw.get("code", "P")
    shares = kw.get("shares", 1000.0)
    price = kw.get("price", 100.0)
    unpriced = kw.get("unpriced", False)
    title = kw.get("title")
    record_fact(
        session,
        kind=FactKind.INSIDER_TRADE,
        symbol=symbol,
        source="sec_edgar",
        source_ref=f"https://www.sec.gov/Archives/edgar/data/0/{accession}/form4.xml",
        dedupe_key=f"form4|{accession}|0",
        known_at=accepted,
        effective_at=datetime.combine(traded, datetime.min.time()),
        payload={
            "accession": accession, "row_index": 0, "form": "4", "filing_date": accepted.date().isoformat(),
            "period_of_report": traded.isoformat(), "is_amendment": False, "issuer_cik": 1, "symbol": symbol,
            "table": "non_derivative", "transaction_date": traded.isoformat(), "code": code,
            "acquired_disposed": "A" if code == "P" else "D", "shares": shares,
            "price": None if unpriced else price, "value": None if unpriced else shares * price,
            "owner_name": owner, "owner_key": owner.lower(), "is_officer": title is not None,
            "is_director": kw.get("director", False), "is_ten_percent_owner": kw.get("ten", False),
            "is_other": False, "officer_title": title, "is_10b5_1": kw.get("plan", False),
        },
    )


def seed(ctx) -> None:
    sys.path.insert(0, str(BACKEND))
    from sqlmodel import Session, create_engine

    from app.knowledge import FactKind, record_fact

    engine = create_engine(f"sqlite:///{ctx.db_path}")
    with Session(engine) as s:
        t = lambda *a, **k: _trade(s, record_fact, FactKind, *a, **k)  # noqa: E731
        t("AAPL", "x1", "Alex Example", 12, 10, title="Chief Executive Officer", shares=5000, price=190.0)
        t("AAPL", "x2", "Dana Director", 10, 8, director=True, shares=1500, price=191.5)
        t("AAPL", "x3", "Chris Finance", 6, 5, title="Chief Financial Officer", code="S", plan=True, shares=3000, price=195.0)
        t("NVDA", "x4", "Pat Owner", 20, 15, ten=True, shares=40000, price=120.0)
        t("NVDA", "x5", "Sam Vice", 3, 2, title="Vice President", unpriced=True, shares=800)
        t("MSFT", "x6", "Lee Exec", 40, 38, title="Chief Operating Officer", code="S", shares=2000, price=420.0)
        s.commit()
    engine.dispose()
    ctx.log("seeded 6 synthetic insider trades")
