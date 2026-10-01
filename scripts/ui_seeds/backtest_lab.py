"""ui_check hook: a finished Backtest Lab run with a random-entry baseline.

    backend/.venv/Scripts/python scripts/ui_check.py --be-port 8122 --fe-port 5222 \
        --routes /backtests "/backtests?run=1" --seed scripts/ui_seeds/backtest_lab.py --out <dir>

The seed writes synthetic price history (a wiggly uptrend for NVDA and AAPL, SPY and a calm
VIX) into the throwaway history store, starts a two-year run through the real API with a
3-seed baseline, and waits for it to finish. Nothing touches the network or the real data.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
DAYS = 800
RUN_FROM, RUN_TO = 280, 780


def seed(ctx) -> None:
    sys.path.insert(0, str(BACKEND))
    from app.data_providers.history_store import HistoryStore
    from tests.backtest_helpers import standard_book, trading_days_from, wiggly_uptrend
    from datetime import date

    import numpy as np

    def swinging(start: float, drift: float, period: int):
        t = np.arange(DAYS)
        return start * np.cumprod(1 + drift + 0.012 * np.sin(2 * np.pi * t / period) + 0.004 * np.sin(t * 1.7))

    days = trading_days_from(date(2022, 1, 3), DAYS)
    book = standard_book(
        days,
        {"NVDA": swinging(100.0, 0.004, 40), "AAPL": swinging(150.0, 0.002, 55)},
    )
    store = HistoryStore(ctx.settings_path.parent / "history.db")
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    for symbol, series in book.series.items():
        store._upsert(symbol, series.frame, "yfinance", now, covered_from=days[0])
    store.close()

    body = {
        "symbols": ["NVDA", "AAPL"],
        "start": days[RUN_FROM].isoformat(),
        "end": days[RUN_TO].isoformat(),
        "decision_every_n_days": 1,
        # A smooth synthetic series rarely clears the live confidence bar; lowering it
        # and adding a time limit is what gives the page trades of every kind to draw.
        "overrides": {"min_confidence_for_trade": 0, "max_holding_days": 15},
        "run_baseline": True,
        "baseline_runs": 3,
    }
    response = ctx.api("POST", "/api/backtests", json=body)
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        run = ctx.api("GET", f"/api/backtests/{run_id}").json()
        if run["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(2)
    assert run["status"] == "done", run
    ctx.log(f"seeded run {run_id}: {run['summary'].get('trade_count')} trades")


def interact(page, ctx) -> None:
    """Screenshot the page section by section (a full-page capture of a long page is unreadable)."""
    page.goto(ctx.fe_url + "/backtests?run=1")
    page.wait_for_selector("text=Equity versus SPY", timeout=30000)
    page.wait_for_timeout(1500)
    page.screenshot(path=str(ctx.out_dir / "hook-top.png"))
    for name in ("Equity versus SPY", "Return by calendar year", "Trades (", "Scorecard", "Random-entry baseline", "What this run tested"):
        page.get_by_role("heading", name=name).first.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        page.screenshot(path=str(ctx.out_dir / f"hook-{name.split()[0].strip('(')}-{name.split()[-1].strip('(')}.png"))
