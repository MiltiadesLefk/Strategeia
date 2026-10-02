"""Pauses placed on a sleeve by a kill switch or a drift alarm.

A pause stops the sleeve OPENING new positions; nothing here closes anything.
One row per pause, kept for history: `resolved_at` is set when the pause is
lifted by hand (a pause never lifts itself, so a bad stretch cannot quietly
resume trading).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, Session, SQLModel, select

from app.timeutil import utcnow_naive


class SleevePause(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sleeve_key: str = Field(index=True)  # "core" for the built-in sleeve
    reason: str  # "drawdown" | "drift"
    detail: str  # the numbers behind it, in words
    paused_at: datetime = Field(default_factory=utcnow_naive)
    resolved_at: Optional[datetime] = Field(default=None, index=True)
    alert_sent: bool = False


def active_pause(session: Session, sleeve_key: str) -> SleevePause | None:
    """The sleeve's current pause, if one is in force."""
    return session.exec(
        select(SleevePause)
        .where(SleevePause.sleeve_key == sleeve_key, SleevePause.resolved_at.is_(None))  # type: ignore[union-attr]
        .order_by(SleevePause.id.desc())  # type: ignore[union-attr]
    ).first()
