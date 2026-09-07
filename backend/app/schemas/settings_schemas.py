from __future__ import annotations

from pydantic import BaseModel, Field


class SettingsUpdateRequest(BaseModel):
    llm_provider: str | None = None
    openrouter_api_key: str | None = None
    openrouter_model: str | None = None
    orcarouter_api_key: str | None = None
    orcarouter_model: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    finnhub_enabled: bool | None = None
    finnhub_api_key: str | None = None
    scan_universe_size: int | None = Field(default=None, gt=0)
    paper_starting_cash: float | None = Field(default=None, gt=0)
    default_risk_pct: float | None = Field(default=None, gt=0, le=100)
    mark_to_market_interval_minutes: int | None = Field(default=None, gt=0)


class TestConnectionRequest(BaseModel):
    target: str  # "llm" | "finnhub"


class TestConnectionResponse(BaseModel):
    ok: bool
    message: str
