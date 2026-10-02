from __future__ import annotations

from app.schemas.common import UtcDatetime
from pydantic import BaseModel


class SleevePauseSchema(BaseModel):
    id: int
    sleeve_key: str
    reason: str
    detail: str
    paused_at: UtcDatetime
    resolved_at: UtcDatetime | None = None
    alert_sent: bool = False
