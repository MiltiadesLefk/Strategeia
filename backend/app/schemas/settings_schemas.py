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
    ai_trading_overlay_enabled: bool | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    scan_universe_size: int | None = Field(default=None, gt=0)
    paper_starting_cash: float | None = Field(default=None, gt=0)
    default_risk_pct: float | None = Field(default=None, gt=0, le=100)
    mark_to_market_interval_minutes: int | None = Field(default=None, gt=0)
    auto_execute_trade_plans: bool | None = None
    auto_scan_enabled: bool | None = None
    max_concurrent_positions: int | None = Field(default=None, gt=0)


class TestConnectionRequest(BaseModel):
    target: str  # "llm" | "finnhub" | "telegram"


class TestConnectionResponse(BaseModel):
    ok: bool
    message: str


class StatusResponse(BaseModel):
    """Mirrors the sidebar's 'Trading Bot Online' indicator shape for four more
    signals: whether the configured LLM provider is real (not just the
    NullLLMProvider echo fallback), whether the AI Trading Overlay will
    actually produce a second opinion right now (enabled AND a real provider
    is configured — matches _maybe_get_ai_opinion's own gate in
    trade_plan_service.py, not just whether the checkbox is on), whether
    Finnhub is configured, and whether Telegram notifications are configured."""

    ai_online: bool
    ai_provider: str
    ai_overlay_online: bool
    finnhub_online: bool
    telegram_online: bool
