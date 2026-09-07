from __future__ import annotations

from pydantic import BaseModel


class SettingsUpdateRequest(BaseModel):
    llm_provider: str | None = None
    openrouter_api_key: str | None = None
    openrouter_model: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    finnhub_enabled: bool | None = None
    finnhub_api_key: str | None = None
    scan_universe_size: int | None = None
    paper_starting_cash: float | None = None
    default_risk_pct: float | None = None
    mark_to_market_interval_minutes: int | None = None


class TestConnectionRequest(BaseModel):
    target: str  # "llm" | "finnhub"


class TestConnectionResponse(BaseModel):
    ok: bool
    message: str
