"""ui_check hook: one finished Rate all run with every kind of row for the Ratings page.

    backend/.venv/Scripts/python scripts/ui_check.py --be-port 8170 --fe-port 5270 --routes /ratings \
        --seed scripts/ui_seeds/ratings.py --out <dir>

Writes made-up rows straight into the throwaway database (two universe ratings, one stock outside the universe that joined,
two market-news headlines, two smart-money rows), then opens that run and screenshots it. Nothing touches the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
RUN = "seedrun0001"


def seed(ctx) -> None:
    sys.path.insert(0, str(BACKEND))
    from sqlmodel import Session, create_engine

    from app.portfolio.models import RatingRecord, RunUsage

    parts = {"technical": 6, "fundamentals": 3, "news": 2}
    why = {"technical": "above the 200-day", "fundamentals": "profitable", "news": "two positive headlines"}
    detail = {"points": 11, "max": 33, "percent": 33, "bar_pct": 30, "parts": parts, "why": why, "items": {}}
    engine = create_engine(f"sqlite:///{ctx.db_path}")
    with Session(engine) as s:
        for sym, pts, passed in (("AAPL", 11, True), ("VST", 4, False)):
            s.add(RatingRecord(run_id=RUN, source="manual", symbol=sym, lane="quality", points=pts, max_points=33, percent=round(pts / 33 * 100),
                               passed=passed, note=f"{pts}/33 points", detail_json=json.dumps(detail)))
        s.add(RatingRecord(run_id=RUN, source="manual", symbol="APP", lane="big news", points=12, max_points=33, percent=36, passed=True,
                           note="12/33 points", detail_json=json.dumps({**detail, "found": "Big news: wins a Navy contract"})))
        news = [{"text": "Acme wins a $400 million Navy contract", "meta": "contract · high", "url": "", "sentiment": "positive"},
                {"text": "Beta Corp raises full-year guidance", "meta": "guidance · medium", "url": "https://example.com/beta", "sentiment": "positive"}]
        for sym in ("ACME", "BETA"):
            s.add(RatingRecord(run_id=RUN, source="manual", symbol=sym, lane="market news", note=f"Wire headline read: {news[0]['text']}",
                               detail_json=json.dumps({"items": {"market news": news[:1] if sym == "ACME" else news[1:]}})))
        for sym, score in (("NXPI", 2.5), ("ON", 1.5)):
            s.add(RatingRecord(run_id=RUN, source="manual", symbol=sym, lane="smart money trades", note=f"Score {score}: analysed, not added",
                               detail_json=json.dumps({"items": {"smart money trades": [{"text": "Director bought $1.8M", "meta": "", "url": ""}]}})))
        s.add(RunUsage(run_id=RUN, calls=31, tokens_in=74000, tokens_out=21000, five_hour_before=4.0, five_hour_after=5.5))
        s.commit()


def interact(page, ctx) -> None:
    page.goto(f"{ctx.fe_url}/ratings?view={RUN}")
    page.wait_for_selector("text=Market news read", timeout=15000)
    ctx.screenshot(page, "ratings-run")
