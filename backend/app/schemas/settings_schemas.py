from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import AiOverlayObjectionAction, ResearchMode, WatchersAction, normalize_api_model, normalize_claude_cli_model, normalize_clock_time
from app.llm_providers.base import LLMTier

# Longest member name accepted in the Congress follow list (real names are well under 60).
MAX_FOLLOWED_MEMBER_NAME_LENGTH = 80

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
    # Decision-tier models: what answers the AI overlay's verdict. "" means
    # "use the routine model"; None means "leave unchanged".
    claude_cli_decision_model: str | None = None
    openrouter_decision_model: str | None = None
    orcarouter_decision_model: str | None = None
    openai_decision_model: str | None = None
    gemini_decision_model: str | None = None
    finnhub_enabled: bool | None = None
    finnhub_api_key: str | None = None
    ai_trading_overlay_enabled: bool | None = None
    # How much the overlay's opinion counts (see AppSettings): whether an
    # objection costs confidence points, and what it does to the trade.
    # Both were previously settable only by hand-editing settings.json.
    ai_overlay_scores_confidence: bool | None = None
    ai_overlay_objection_action: AiOverlayObjectionAction | None = None
    min_confidence_for_trade: int | None = Field(default=None, ge=0, le=100)
    research_mode: ResearchMode | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    thesis_alerts: bool | None = None
    committee_max_llm_calls: int | None = Field(default=None, ge=6, le=40)
    committee_debate_rounds: int | None = Field(default=None, ge=1, le=3)
    committee_risk_rounds: int | None = Field(default=None, ge=1, le=3)
    scan_universe_size: int | None = Field(default=None, gt=0)
    news_cards_enabled: bool | None = None
    news_card_batch_limit: int | None = Field(default=None, ge=1, le=30)
    paper_starting_cash: float | None = Field(default=None, gt=0)
    default_risk_pct: float | None = Field(default=None, gt=0, le=100)
    mark_to_market_interval_minutes: int | None = Field(default=None, gt=0)
    auto_execute_trade_plans: bool | None = None
    auto_scan_enabled: bool | None = None
    watchers_enabled: bool | None = None
    watchers_action: WatchersAction | None = None
    watchers_poll_minutes: int | None = Field(default=None, ge=1, le=60)
    # CIK numbers of the funds whose 13F filings are followed (digits only, at most 50).
    smart_money_followed_funds: list[str] | None = Field(default=None, max_length=50)
    # Congress trades: follow every member ("all") or only the listed ones ("list").
    smart_money_follow_congress: Literal["all", "list"] | None = None
    smart_money_followed_members: list[str] | None = Field(default=None, max_length=50)
    max_concurrent_positions: int | None = Field(default=None, gt=0)
    # Trading days before a stalled position is closed at the close; 0 = no limit.
    # Capped at 60 (about three months): the exit scan reads 3 months of bars
    # and has to see the entry bar to count days from it.
    max_holding_days: int | None = Field(default=None, ge=0, le=60)
    # Notifications: morning note, weekly digest, price alerts on open positions.
    morning_note_enabled: bool | None = None
    morning_note_time_et: str | None = None
    weekly_digest_enabled: bool | None = None
    price_alert_positions_enabled: bool | None = None
    price_alert_stop_atr: float | None = Field(default=None, gt=0, le=10)
    price_alert_tp1_pct: float | None = Field(default=None, gt=0, le=20)

    @field_validator("morning_note_time_et")
    @classmethod
    def _validate_morning_note_time_et(cls, value: str | None) -> str | None:
        return None if value is None else normalize_clock_time(value)

    @field_validator("smart_money_followed_members")
    @classmethod
    def _validate_smart_money_followed_members(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            text = " ".join(str(raw).split())
            if not text or len(text) > MAX_FOLLOWED_MEMBER_NAME_LENGTH:
                raise ValueError(f"member names must be 1 to {MAX_FOLLOWED_MEMBER_NAME_LENGTH} characters")
            key = text.lower()
            if key not in seen:
                seen.add(key)
                cleaned.append(text)
        return cleaned

    @field_validator("smart_money_followed_funds")
    @classmethod
    def _validate_smart_money_followed_funds(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        for raw in value:
            text = str(raw).strip()
            if not text.isdigit() or len(text) > 10:
                raise ValueError("fund CIKs must be digits only (at most 10)")
            text = text.lstrip("0") or "0"
            if text not in cleaned:
                cleaned.append(text)
        return cleaned

    @field_validator("claude_cli_model")
    @classmethod
    def _validate_claude_cli_model(cls, value: str | None) -> str | None:
        return None if value is None else normalize_claude_cli_model(value)

    @field_validator("claude_cli_decision_model")
    @classmethod
    def _validate_claude_cli_decision_model(cls, value: str | None) -> str | None:
        return None if value is None else normalize_claude_cli_model(value)

    # Model ids reach a JSON body, or (Gemini) a URL path, so every one is
    # checked here, routine and decision alike, before it is saved.
    @field_validator(
        "openrouter_model",
        "orcarouter_model",
        "openai_model",
        "openrouter_decision_model",
        "orcarouter_decision_model",
        "openai_decision_model",
    )
    @classmethod
    def _validate_api_model(cls, value: str | None) -> str | None:
        return None if value is None else normalize_api_model(value)

    @field_validator("gemini_model", "gemini_decision_model")
    @classmethod
    def _validate_gemini_model(cls, value: str | None) -> str | None:
        return None if value is None else normalize_api_model(value, allow_slash=False)


class TestConnectionOverrides(BaseModel):
    """What is typed in a Settings card but not saved yet. A test button sends
    these so it tests what the form shows, not what happens to be on disk.

    Nothing here is ever persisted. A field left out (None) means "use the saved
    value". For secrets, the provider and the chat id, a blank value also means
    "use the saved value", and so does a masked hint such as "••••ab12" (that is
    what the settings API shows for a saved secret, never the secret itself). A
    model name is the exception: blank is a real choice there ("don't pin" for
    the Claude CLI, "use the routine model" for a decision model), so "" is
    tested as typed. Unknown keys are rejected so a typo cannot silently test
    the saved value instead.
    """

    model_config = ConfigDict(extra="forbid")

    llm_provider: str | None = None
    claude_cli_model: str | None = None
    claude_cli_decision_model: str | None = None
    openrouter_api_key: str | None = None
    openrouter_model: str | None = None
    openrouter_decision_model: str | None = None
    orcarouter_api_key: str | None = None
    orcarouter_model: str | None = None
    orcarouter_decision_model: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    openai_decision_model: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    gemini_decision_model: str | None = None
    finnhub_api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    @field_validator("claude_cli_model", "claude_cli_decision_model")
    @classmethod
    def _validate_claude_cli_model(cls, value: str | None) -> str | None:
        return None if value is None else normalize_claude_cli_model(value)

    @field_validator(
        "openrouter_model",
        "orcarouter_model",
        "openai_model",
        "openrouter_decision_model",
        "orcarouter_decision_model",
        "openai_decision_model",
    )
    @classmethod
    def _validate_api_model(cls, value: str | None) -> str | None:
        return None if value is None else normalize_api_model(value)

    @field_validator("gemini_model", "gemini_decision_model")
    @classmethod
    def _validate_gemini_model(cls, value: str | None) -> str | None:
        return None if value is None else normalize_api_model(value, allow_slash=False)


class TestConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str  # "llm" | "finnhub" | "telegram"
    # Which model tier to test when target is "llm": the routine model (the
    # narratives) or the decision model (the AI overlay's verdict).
    tier: LLMTier = "routine"
    # Unsaved form values to test instead of the saved ones (see the class).
    overrides: TestConnectionOverrides | None = None


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
    # The model that answers the AI overlay's verdict (its decision tier), but
    # only when it is a different model from the routine one. "" when no
    # decision model is set (both tiers use the routine model) or there is no
    # AI provider.
    ai_decision_model: str = ""
    ai_overlay_online: bool
    finnhub_online: bool
    telegram_online: bool
