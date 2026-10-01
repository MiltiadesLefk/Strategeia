from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import get_session, require_auth
from app.portfolio.calibration import compute_calibration
from app.schemas.calibration_schemas import CalibrationReportSchema

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"], dependencies=[Depends(require_auth)])


@router.get("/calibration", response_model=CalibrationReportSchema)
def calibration(session: Session = Depends(get_session)) -> CalibrationReportSchema:
    """Does the confidence score predict results? Read-only: computed from the
    closed trades already stored. It does not run the exit check or touch the
    equity curve, so opening the Portfolio page never changes the report."""
    return CalibrationReportSchema.model_validate(compute_calibration(session))
