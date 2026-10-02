from __future__ import annotations

from pydantic import BaseModel


class PresetCriterionSchema(BaseModel):
    id: str
    label: str
    required: bool


class PresetUnavailableSchema(BaseModel):
    label: str
    reason: str


class PresetSchema(BaseModel):
    name: str
    label: str
    description: str
    criteria: list[PresetCriterionSchema]
    # How many criteria (in all, required ones included) a symbol must meet to match.
    min_matches: int
    # Criteria from the source screen that our data cannot answer, with why.
    unavailable: list[PresetUnavailableSchema]


class PresetCriterionResultSchema(BaseModel):
    id: str
    label: str
    # True met, False not met, null could not be told (the data was missing).
    ok: bool | None
    detail: str


class PresetMatchSchema(BaseModel):
    symbol: str
    price: float | None
    change_pct_24h: float | None
    criteria: list[PresetCriterionResultSchema]


class PresetRunResponse(BaseModel):
    preset: PresetSchema
    matches: list[PresetMatchSchema]
    # How the run was bounded: the effective watchlist has `universe_size` symbols, the first
    # `checked` were screened (never more than `limit`).
    universe_size: int
    limit: int
    checked: int
    # Symbols a required criterion could not be answered for, so they were neither matched nor rejected.
    unjudged: list[str]
    errors: list[str] = []
    note: str
