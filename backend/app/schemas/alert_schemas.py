from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime

AlertConditionName = Literal["price_above", "price_below", "day_move_pct", "near_stop", "near_tp1"]
AlertUnitName = Literal["pct", "atr"]


class PriceAlertCreateRequest(BaseModel):
    # The symbol and the threshold are checked again in price_alert_service (one place for
    # the rules), so a script and the UI get the same messages.
    symbol: str = Field(min_length=1, max_length=20)
    condition: AlertConditionName
    threshold: float = Field(gt=0, allow_inf_nan=False)
    unit: AlertUnitName | None = None
    repeat: bool = False
    cooldown_minutes: int = Field(default=240, ge=5, le=7 * 24 * 60)
    note: str | None = Field(default=None, max_length=200)


class PriceAlertSchema(BaseModel):
    id: int
    symbol: str
    condition: str
    threshold: float
    unit: str | None
    status: str
    repeat: bool
    cooldown_minutes: int
    note: str | None
    created_at: UtcDatetime
    triggered_at: UtcDatetime | None
    last_triggered_at: UtcDatetime | None
    trigger_count: int
    last_value: float | None
    cancelled_at: UtcDatetime | None

    model_config = {"from_attributes": True}


class PriceAlertListResponse(BaseModel):
    alerts: list[PriceAlertSchema]
    active_count: int
    max_active: int


class PriceAlertDeleteResponse(BaseModel):
    id: int
    result: Literal["cancelled", "deleted"]


class NotePreviewResponse(BaseModel):
    text: str
    ai_used: bool
    ai_provider: str | None
    unavailable: list[str]
    generated_at: UtcDatetime
    parts: int  # how many Telegram messages it would take


class NoteSendResponse(BaseModel):
    ok: bool
    message: str
    parts_sent: int
    ai_used: bool
