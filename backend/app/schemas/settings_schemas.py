from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.config import AiOverlayObjectionAction, normalize_claude_cli_model


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
    # Model the Claude Code CLI is pinned to. "" is accepted on purpose and
    # means "don't pin"; None means "leave unchanged" like every other field.
    claude_cli_model: str | None = None
    finnhub_enabled: bool | None = None
    finnhub_api_key: str | None = None
    ai_trading_overlay_enabled: bool | None = None
    # How much the overlay's opinion counts (see AppSettings): whether an
    # objection costs confidence points, and what it does to the trade.
    # Both were previously settable only by hand-editing settings.json.
    ai_overlay_scores_confidence: bool | None = None
    ai_overlay_objection_action: AiOverlayObjectionAction | None = None
    min_confidence_for_trade: int | None = Field(default=None, ge=0, le=100)
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    scan_universe_size: int | None = Field(default=None, gt=0)
    paper_starting_cash: float | None = Field(default=None, gt=0)
    default_risk_pct: float | None = Field(default=None, gt=0, le=100)
    mark_to_market_interval_minutes: int | None = Field(default=None, gt=0)
    auto_execute_trade_plans: bool | None = None
    auto_scan_enabled: bool | None = None
    max_concurrent_positions: int | None = Field(default=None, gt=0)
    # Trading days before a stalled position is closed at the close; 0 = no limit.
    # Capped at 60 (about three months): the exit scan reads 3 months of bars
    # and has to see the entry bar to count days from it.
    max_holding_days: int | None = Field(default=None, ge=0, le=60)

    @field_validator("claude_cli_model")
    @classmethod
    def _validate_claude_cli_model(cls, value: str | None) -> str | None:
        return None if value is None else normalize_claude_cli_model(value)


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
    # The model the provider is pinned to, shown next to the provider name
    # ("claude_code_cli (sonnet)"). "" when the provider has no pin setting or
    # the pin is blank (the CLI's own default).
    ai_model: str = ""
    ai_overlay_online: bool
    finnhub_online: bool
    telegram_online: bool
