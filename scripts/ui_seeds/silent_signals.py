"""ui_check hook: a trade plan carrying silent signals.

    backend/.venv/Scripts/python scripts/ui_check.py --be-port 8125 --fe-port 5225 \
        --routes /trade-plans --seed scripts/ui_seeds/silent_signals.py --out <dir>

Writes one pending plan and one no-trade record, both with a recorded FINRA short-volume
reading, straight into the throwaway database. Nothing touches the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"

SIGNALS = [
    {
        "name": "finra_short_volume",
        "value": "58.2% short volume (baseline 46.0%)",
        "would_score": -1,
        "reason": "Unusually high short volume, mildly bearish context. Not short interest.",
        "available": True,
    },
]


def seed(ctx) -> None:
    sys.path.insert(0, str(BACKEND))
    from sqlmodel import Session, create_engine

    from app.portfolio.models import TradePlanRecord

    engine = create_engine(f"sqlite:///{ctx.db_path}")
    stored = json.dumps(SIGNALS)
    with Session(engine) as session:
        session.add(TradePlanRecord(
            symbol="NVDA", direction="long", entry=100.0, stop=95.0, tp1=110.0, tp2=120.0, rr1=2.0, rr2=4.0,
            suggested_shares=20, account_risk_dollars=100.0, confidence_score=62, ai_take_text="Rule-based take.",
            ai_provider="none", technical_score=5, status="pending", shadow_signals=stored,
            signal_reasons="Strong trend; volume above average",
        ))
        session.add(TradePlanRecord(
            symbol="AAPL", status="no_trade", reason="Confidence too low.", confidence_score=19,
            shadow_signals=json.dumps([{**SIGNALS[0], "available": False, "value": None, "would_score": 0,
                                        "reason": "No data."}]),
        ))
        session.commit()
    engine.dispose()
    ctx.log("seeded 2 plans with silent signals")


def interact(page, ctx) -> None:
    """Show each stored plan as the result of "Generate Trade Plan" (the generate call is
    answered from the database, so no market data or LLM is involved)."""
    plans = {p["symbol"]: p for p in ctx.api("GET", "/api/trade-plans?status=all").json()}
    current = {"symbol": "NVDA"}
    page.route(
        "**/api/trade-plans/generate",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(plans[current["symbol"]])),
    )
    for symbol in ("NVDA", "AAPL"):
        current["symbol"] = symbol
        page.goto(f"{ctx.fe_url}/trade-plans?symbol={symbol}")
        page.get_by_role("button", name="Generate Trade Plan").click()
        page.wait_for_selector("[data-testid=silent-signals]", timeout=15000)
        text = page.inner_text("[data-testid=silent-signals]")
        assert "Silent signals (not scored)" in text, text
        ctx.log(f"{symbol}: {text!r}")
        ctx.screenshot(page, f"silent-signals-{symbol}")
