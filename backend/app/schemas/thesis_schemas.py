from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime

PillarStatus = Literal["intact", "at_risk", "broken"]
ItemKind = Literal["pillar", "risk", "catalyst"]


class ThesisPillarSchema(BaseModel):
    id: str
    # What the rules can re-check: "trend", "stop", "weekly", "market". "reason" and "ai_overlay"
    # pillars record what the plan cited and are not re-checked; "user" is typed in by the user.
    key: str
    text: str
    status: PillarStatus
    source: str
    # A core pillar breaking sets the "thesis broken" warning (the trend, for the trade's direction).
    core: bool = False
    # What the last re-check saw, in plain words; null before the first re-check.
    detail: str | None = None
    checked_at: str | None = None


class ThesisRiskSchema(BaseModel):
    id: str
    text: str
    source: str


class ThesisCatalystSchema(BaseModel):
    id: str
    key: str
    text: str
    # ISO date; null for a catalyst with no date (a price target).
    date: str | None = None
    days_until: int | None = None
    source: str


class ThesisLogEntrySchema(BaseModel):
    at: str
    kind: Literal["note", "system"]
    text: str


class ThesisSchema(BaseModel):
    id: int
    position_id: int
    trade_plan_id: int | None
    symbol: str
    direction: str
    pillars: list[ThesisPillarSchema]
    risks: list[ThesisRiskSchema]
    catalysts: list[ThesisCatalystSchema]
    log: list[ThesisLogEntrySchema]
    thesis_broken: bool
    broken_at: UtcDatetime | None
    last_checked_at: UtcDatetime | None
    review_text: str | None
    review_at: UtcDatetime | None
    review_provider: str | None
    review_model: str | None
    review_error: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ThesisResponse(BaseModel):
    """`thesis` is null for a position that has none yet (opened before the tracker existed, or
    not seeded yet): a read never creates one."""

    thesis: ThesisSchema | None


class ThesisRecheckResponse(BaseModel):
    thesis: ThesisSchema
    # "checked", or "skipped" with `note` saying why (price history could not be fetched).
    status: str
    changes: list[str] = []
    note: str = ""


class ThesisNoteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ThesisItemRequest(BaseModel):
    kind: ItemKind
    text: str = Field(min_length=1, max_length=600)
    # Catalysts only: an ISO date (YYYY-MM-DD) or empty for none.
    date: str | None = None
    # Pillars only; defaults to intact.
    status: PillarStatus | None = None


class ThesisItemUpdateRequest(BaseModel):
    status: PillarStatus
