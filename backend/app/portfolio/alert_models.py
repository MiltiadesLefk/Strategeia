"""Tables for price alerts and for "already sent" bookkeeping of notifications.

Alerts only ever send a message and record a dated fact. Nothing here can open, change
or close a paper position.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from sqlmodel import Field, SQLModel

from app.timeutil import utcnow_naive

AlertStatus = Literal["active", "triggered", "cancelled"]
AlertCondition = Literal["price_above", "price_below", "day_move_pct", "near_stop", "near_tp1"]
AlertUnit = Literal["pct", "atr"]


class PriceAlert(SQLModel, table=True):
    """One alert a person asked for.

    - `condition` and `threshold`: "price_above"/"price_below" compare the live price with
      `threshold` (a price); "day_move_pct" fires when the price has moved at least
      `threshold` percent either way since the previous close; "near_stop" / "near_tp1"
      fire when the price of an OPEN position in this symbol is within `threshold` of its
      stop / first target, measured in `unit` ("pct" of the price, or "atr" = multiples of
      the average daily range).
    - `repeat`: a one-shot alert becomes "triggered" the first time it fires and never
      again. A repeating one stays "active" and fires again only after `cooldown_minutes`.
    - Times are naive UTC like every other column.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    condition: str
    threshold: float
    unit: Optional[str] = None
    status: str = Field(default="active", index=True)
    repeat: bool = False
    cooldown_minutes: int = 240
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow_naive)
    # The first time it fired, the latest time it fired, how often, and the value seen then.
    triggered_at: Optional[datetime] = None
    last_triggered_at: Optional[datetime] = None
    trigger_count: int = 0
    last_value: Optional[float] = None
    cancelled_at: Optional[datetime] = None


class NotificationLog(SQLModel, table=True):
    """When a recurring notification was last sent, so a restart or a second scheduler
    tick cannot send it twice. `key` examples: "morning_note", "weekly_digest",
    "position_alert:12:near_stop". `last_day` is the US Eastern date (YYYY-MM-DD) of
    the last successful send."""

    key: str = Field(primary_key=True)
    last_day: Optional[str] = None
    last_sent_at: Optional[datetime] = None
    # The latest attempt, successful or not: a failed send is retried only after a pause.
    last_attempt_at: Optional[datetime] = None
    last_error: Optional[str] = None
