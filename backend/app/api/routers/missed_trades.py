from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import get_session, require_auth
from app.portfolio.missed_trade_report import build_missed_trade_report
from app.schemas.missed_trade_schemas import MissedTradeReportSchema

router = APIRouter(prefix="/api/missed-trades", tags=["missed-trades"], dependencies=[Depends(require_auth)])


@router.get("", response_model=MissedTradeReportSchema)
def missed_trades_report(session: Session = Depends(get_session)) -> MissedTradeReportSchema:
    """What the trades we did not take would have earned, from the stored (historical)
    outcomes. Read-only; nothing computes new ones any more (hold mode declines nothing)."""
    return MissedTradeReportSchema.model_validate(build_missed_trade_report(session))
