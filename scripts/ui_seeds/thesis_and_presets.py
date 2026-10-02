"""ui_check hook: one open position with a seeded thesis (one pillar broken, one at risk).

    backend/.venv/Scripts/python scripts/ui_check.py --be-port 8137 --fe-port 5237 \
        --routes /portfolio /scan --seed scripts/ui_seeds/thesis_and_presets.py --out <dir>

Writes straight into the throwaway database; nothing touches the network or real data.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"


class _NoData:
    def get_earnings_date(self, symbol):
        return date.today() + timedelta(days=2)


def seed(ctx) -> None:
    sys.path.insert(0, str(BACKEND))
    from sqlmodel import Session, SQLModel, create_engine

    from app.config import AppSettings
    from app.portfolio.models import PaperPosition, TradePlanRecord
    from app.services import thesis_service

    engine = create_engine(f"sqlite:///{ctx.db_path}")
    from app import database  # noqa: F401
    from app.portfolio import thesis_models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        plan = TradePlanRecord(
            symbol="NVDA", direction="long", entry=100.0, stop=92.0, tp1=112.0, tp2=120.0, confidence_score=69,
            signal_reasons="Bullish trend with strong momentum; weekly timeframe also bullish; insider buying",
        )
        session.add(plan)
        session.commit()
        session.refresh(plan)
        position = PaperPosition(
            trade_plan_id=plan.id, symbol="NVDA", direction="long", entry_price=100.0, stop_loss=92.0,
            tp1=112.0, tp2=120.0, shares=10,
        )
        session.add(position)
        session.commit()
        session.refresh(position)
        thesis = thesis_service.seed_thesis(session, position, _NoData(), AppSettings())
        pillars = thesis_service._load(thesis.pillars)
        pillars[0]["status"] = "broken"
        pillars[1]["status"] = "at_risk"
        thesis.pillars = thesis_service._dump(pillars)
        thesis.thesis_broken = True
        session.add(thesis)
        session.commit()
    ctx.log("seeded NVDA position with a broken thesis")
