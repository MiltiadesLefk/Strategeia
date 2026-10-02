from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.deps import get_session, require_auth
from app.schemas.alert_schemas import (
    PriceAlertCreateRequest,
    PriceAlertDeleteResponse,
    PriceAlertListResponse,
    PriceAlertSchema,
)
from app.services import price_alert_service as alerts

router = APIRouter(prefix="/api/alerts", tags=["alerts"], dependencies=[Depends(require_auth)])


@router.get("", response_model=PriceAlertListResponse)
def list_price_alerts(
    status: str | None = Query(None, pattern="^(active|triggered|cancelled)$"),
    session: Session = Depends(get_session),
) -> PriceAlertListResponse:
    """The alerts you set, newest first. Read-only: opening the list never evaluates anything."""
    return PriceAlertListResponse(
        alerts=[PriceAlertSchema.model_validate(a) for a in alerts.list_alerts(session, status)],
        active_count=alerts.active_alert_count(session),
        max_active=alerts.MAX_ACTIVE_ALERTS,
    )


@router.post("", response_model=PriceAlertSchema, status_code=201)
def create_price_alert(req: PriceAlertCreateRequest, session: Session = Depends(get_session)) -> PriceAlertSchema:
    """Save an alert. It only ever sends a message: nothing is traded or changed."""
    try:
        alert = alerts.create_alert(
            session,
            symbol=req.symbol,
            condition=req.condition,
            threshold=req.threshold,
            unit=req.unit,
            repeat=req.repeat,
            cooldown_minutes=req.cooldown_minutes,
            note=req.note,
        )
    except alerts.AlertValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PriceAlertSchema.model_validate(alert)


@router.delete("/{alert_id}", response_model=PriceAlertDeleteResponse)
def delete_price_alert(alert_id: int, session: Session = Depends(get_session)) -> PriceAlertDeleteResponse:
    """Cancel an active alert (it stays in the list as cancelled); remove one that has
    already triggered or been cancelled."""
    result = alerts.cancel_or_delete_alert(session, alert_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No such alert.")
    return PriceAlertDeleteResponse(id=alert_id, result=result)
