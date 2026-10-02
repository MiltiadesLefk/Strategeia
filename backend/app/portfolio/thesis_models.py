# From Anthropic's financial-services skills (anthropics/financial-services).
# Licensed under the Apache License, Version 2.0; full text in THIRD_PARTY_NOTICES.md.
# Adapted from anthropics/financial-services@574ed36
# plugins/vertical-plugins/equity-research/skills/thesis-tracker/SKILL.md; changes: the skill's
# structure (pillars with a status, risks, a catalyst calendar, a dated update log) became the
# columns below; every pillar carries a machine-readable key so rules can re-check it, and the
# lists are stored as JSON text because they are always read and written as a whole.
"""One thesis per open paper position: why the trade was taken, and whether that still holds.

The thesis is a record of what the plan itself cited (each scored reason is a pillar), what could
go wrong (risks), what is coming up (catalysts, with dates) and a dated log of both the user's own
notes and the system's re-checks. Rules re-check the mechanical pillars (see
services/thesis_service.py); nothing here ever opens, closes or resizes a position.

Lists are JSON text, not child tables: a thesis is small, always shown whole, and an edit replaces
the list. Each item has a short random `id` so the UI can address one without relying on its index.
Times are naive UTC like every other column.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.timeutil import utcnow_naive

PILLAR_INTACT = "intact"
PILLAR_AT_RISK = "at_risk"
PILLAR_BROKEN = "broken"
PILLAR_STATUSES = (PILLAR_INTACT, PILLAR_AT_RISK, PILLAR_BROKEN)

KIND_PILLAR = "pillar"
KIND_RISK = "risk"
KIND_CATALYST = "catalyst"
ITEM_KINDS = (KIND_PILLAR, KIND_RISK, KIND_CATALYST)

SOURCE_PLAN = "plan"  # taken from what the trade plan recorded
SOURCE_RULE = "rule"  # added by the rules (stop, catalysts from the calendar)
SOURCE_USER = "user"  # typed in by the user

LOG_NOTE = "note"  # written by the user
LOG_SYSTEM = "system"  # written by a re-check or another automatic step


class ThesisRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # One thesis per position. Unique so a seed that runs twice (the sweep and a button press)
    # cannot make two.
    position_id: int = Field(foreign_key="paperposition.id", unique=True, index=True)
    # The plan the position came from; None for a position opened without one.
    trade_plan_id: Optional[int] = Field(default=None, foreign_key="tradeplanrecord.id")
    symbol: str
    direction: str

    # JSON lists. pillar: {id, key, text, status, source, core, detail, checked_at}.
    # risk: {id, text, source}. catalyst: {id, key, text, date, source, alerted}.
    # log entry: {at (ISO, UTC), kind: note|system, text}.
    pillars: str = "[]"
    risks: str = "[]"
    catalysts: str = "[]"
    log: str = "[]"

    # True while a core pillar (the trend, for the trade's direction) is broken.
    thesis_broken: bool = False
    broken_at: Optional[datetime] = None
    # When the one-time "thesis broken" alert went out; the alert is never sent twice.
    broken_alerted_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None

    # The optional AI review paragraph (written only when the user presses Review). Null until
    # one is written; review_error is why the last attempt failed, and no template stands in.
    review_text: Optional[str] = None
    review_at: Optional[datetime] = None
    review_provider: Optional[str] = None
    review_model: Optional[str] = None
    review_error: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow_naive)
    updated_at: datetime = Field(default_factory=utcnow_naive)
