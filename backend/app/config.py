from __future__ import annotations

import json
import threading
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class InfraSettings(BaseSettings):
    """Static, process-lifetime config from .env — never editable at runtime."""

    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://localhost:5173"
    # Opt-in stop-gap auth: unset (default) means every endpoint stays open,
    # exactly like before — zero friction for local/dev use. Set
    # API_SHARED_SECRET in .env to require a matching `X-API-Key` header on
    # every API route (see api/deps.py's require_shared_secret). This is not
    # a substitute for the reverse-proxy/TLS/real-auth setup TODO.md's
    # "Before running this on a public VPS 24/7" section calls for — it's a
    # cheap guard against casual unauthenticated abuse (draining a metered
    # LLM/Finnhub key via /api/trade-plans/generate or /api/scan/auto-trade,
    # overwriting Settings to redirect Telegram notifications, wiping the
    # portfolio via /api/portfolio/reset) for anyone who exposes the port
    # before doing that real hardening.
    api_shared_secret: str = ""
    # Deliberately separate from data/ (which holds the bundled, read-only
    # sp500.csv baked into the Docker image) so a single volume mount at
    # runtime/ can persist the db + settings without hiding sp500.csv.
    # SEC EDGAR's fair-access policy requires a User-Agent that identifies the
    # requester with a contact address, and it rejects strings containing a URL
    # (verified: any UA with "github.com" in it returns 403 where the same
    # request with an email-shaped contact returns 200). The default is a
    # neutral placeholder deliberately — set SEC_EDGAR_USER_AGENT in .env to
    # your own contact if you use the insider-scoring feature in earnest.
    sec_edgar_user_agent: str = "Strategeia/1.0 (personal paper-trading research; contact@strategeia.example)"
    db_path: str = "runtime/strategeia.db"
    settings_path: str = "runtime/settings.json"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def db_file(self) -> Path:
        return BASE_DIR / self.db_path

    @property
    def settings_file(self) -> Path:
        return BASE_DIR / self.settings_path


LlmProviderName = Literal["none", "claude_code_cli", "openrouter", "orcarouter", "openai", "gemini"]

# What happens when the AI Trading Overlay says it would not take the trade.
# One choice, not a set of flags: "cancel" and "hold" fire on the identical
# trigger and cancel always wins (a cancelled evaluation never reaches the
# auto-execute step), so as two independent booleans one combination was
# always dead and the UI could show a setting as ON that could never once
# fire. See notes/Decisions.md.
AiOverlayObjectionAction = Literal["cancel", "hold", "none"]


MASK_BULLET_COUNT = 16


def _mask_secret(value: str) -> str:
    """"" when unset; otherwise a masked hint that reveals at most the last
    4 characters — enough to recognize "yes, that's the right key", never
    enough to reconstruct it. Always the same number of bullets regardless of
    the real key's length, so the UI can render a stable-looking masked
    field without also leaking how long the stored secret is."""
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * MASK_BULLET_COUNT
    return f"{'•' * MASK_BULLET_COUNT}{value[-4:]}"


class AppSettings(BaseModel):
    """Runtime-editable config — provider choice, API keys, paper account.

    Persisted to runtime/settings.json (gitignored). Contains secrets: never
    log this model's contents, never echo api keys back in API responses.
    """

    llm_provider: LlmProviderName = "none"
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-3.5-haiku"
    orcarouter_api_key: str = ""
    orcarouter_model: str = "orcarouter/auto"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"

    finnhub_enabled: bool = False
    finnhub_api_key: str = ""

    # Master switch, opt-in and off by default: when on, generate_trade_plan
    # asks the LLM for its own independent read of ALL the same raw data
    # (technicals, fundamentals, news, earnings) — stored as ai_opinion_* on
    # the trade plan. Costs one extra LLM call per symbol evaluated,
    # including ones the rule-based engine rejects. With this off, neither
    # ai_overlay_* setting below does anything. See notes/Decisions.md.
    ai_trading_overlay_enabled: bool = False

    # Two independent questions about how much the overlay's opinion counts,
    # both meaningful only when ai_trading_overlay_enabled is also on — off,
    # neither changes anything, so a default install behaves exactly as it
    # always has. Both default to their active setting because turning the
    # overlay on at all is itself opt-in: the reasonable assumption is you
    # wanted the second opinion to matter. Neither lets the overlay
    # ORIGINATE a trade — direction still comes from the rule-based trend and
    # nowhere else, and the score contribution is one-directional (a penalty,
    # never a bonus). See analysis/ai_overlay_scoring.py and
    # notes/Decisions.md.

    # (1) Does an objection cost confidence points? The overlay becomes a
    # scored dimension like every other confluence check (0 to -3, scaled by
    # the model's own stated conviction) instead of sitting in a box next to
    # a number it couldn't touch. A marginal plan can fall under
    # min_confidence_for_trade this way, so this alone can reach the
    # trade/no-trade decision — independently of the action below.
    ai_overlay_scores_confidence: bool = True

    # What an objection actually DOES, as a single three-way choice:
    #   "cancel" — the evaluation becomes an explicit no_trade decision with
    #              the overlay named as the reason, instead of a plan.
    #   "hold"   — the plan is written normally, but auto-execute leaves it
    #              "pending" for manual review instead of opening the
    #              position. The one moment a second opinion is worth having
    #              is the moment before capital commits.
    #   "none"   — the objection is recorded and scored but stops nothing.
    #              Useful for measuring whether the overlay's objections
    #              actually correlate with losing trades before giving it
    #              stopping power.
    # These were two booleans (ai_overlay_vetoes_trade /
    # ai_overlay_blocks_auto_execute) and should not have been: they fire on
    # the same trigger and cancel strictly wins, so "both on" was
    # indistinguishable from "cancel" and the UI showed a live-looking
    # toggle that could never fire. Old settings.json files are migrated in
    # _migrate_overlay_objection_action below.
    ai_overlay_objection_action: AiOverlayObjectionAction = "cancel"

    # The confidence bar a setup must clear to become a tradeable plan at
    # all; below it (or on a Neutral trend) the evaluation is persisted as an
    # explicit no_trade decision instead. Was a hardcoded constant in
    # trade_plan_service.py.
    #
    # The default is 30, not the 40 that constant held, and the change is a
    # rescale rather than a loosening. Confidence used to be squeezed onto a
    # 20-90 range; it is now the honest 0-100 percentage of achievable
    # evidence points a setup actually earned. 40 on the old range and 30 on
    # the new one are the same cutoff: 5 of the 16 achievable points. Keeping
    # the literal 40 would have silently tightened the bar to 7 points and
    # rejected setups the app has been trading all along.
    min_confidence_for_trade: int = 30

    @model_validator(mode="before")
    @classmethod
    def _migrate_overlay_objection_action(cls, data):
        """Carry a settings.json written before ai_overlay_objection_action
        existed onto the new field. Without this, pydantic silently drops
        the two retired booleans (BaseModel ignores extras) and anyone who
        had deliberately turned the veto off would find it back on after an
        upgrade — a real behaviour change, applied invisibly."""
        if not isinstance(data, dict) or data.get("ai_overlay_objection_action") is not None:
            return data
        vetoes = data.get("ai_overlay_vetoes_trade")
        holds = data.get("ai_overlay_blocks_auto_execute")
        if vetoes is None and holds is None:
            return data
        data = dict(data)
        data["ai_overlay_objection_action"] = "cancel" if vetoes else "hold" if holds else "none"
        return data

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    scan_universe_size: int = 50

    paper_starting_cash: float = 100_000.0
    default_risk_pct: float = 1.0
    mark_to_market_interval_minutes: int = 15

    # Execution realism. A paper fill that always lands exactly on the stop
    # is the single easiest way to make a strategy look better than it is:
    # real stops are market orders that fill at whatever the tape offers
    # after the trigger, which on a gap is materially worse. The engine
    # models the gap itself unconditionally (see PaperTradingEngine._fill_price);
    # these two knobs cover the rest of the cost of doing business.
    # slippage_bps applies only to MARKET fills (entries and stop exits),
    # never to take-profit limit fills. 5bps is a modest, defensible default
    # for liquid large-caps — raise it if you trade thinner names.
    slippage_bps: float = 5.0
    commission_per_trade: float = 0.0
    auto_execute_trade_plans: bool = True

    # Unattended scan -> generate -> execute loop. Off by default — unlike
    # auto-execute (which only acts on a plan you already asked for), this
    # decides *which* symbols to trade with no human in the loop at all, so
    # it opts in rather than opting out. Runs 3x/day at fixed session-open
    # times (see scheduler.py's AUTO_SCAN_SESSION_TIMES_UTC), not on an
    # interval — no `_interval_minutes` setting to configure here.
    auto_scan_enabled: bool = False
    max_concurrent_positions: int = 5

    # Portfolio-level risk, as opposed to the per-trade risk default_risk_pct
    # already covers. Five 1%-risk positions is only "5% at risk" if the five
    # are independent — five semis on the same tape is one 5% bet. max_positions
    # _per_sector bounds that; max_position_pct_of_adv bounds the other
    # direction (a size you could not actually fill without moving the book).
    max_positions_per_sector: int = 2
    max_position_pct_of_adv: float = 1.0

    def redacted(self) -> dict:
        """Copy safe to return over the API — secrets collapsed to a masked
        hint (e.g. "••••ab12") so the UI can show *that* a key is set and
        confirm it's the right one, without ever echoing the real value."""
        data = self.model_dump()
        for key in (
            "openrouter_api_key",
            "orcarouter_api_key",
            "openai_api_key",
            "gemini_api_key",
            "finnhub_api_key",
            "telegram_bot_token",
        ):
            data[key] = _mask_secret(data[key])
        return data


_lock = threading.Lock()
_settings_cache: AppSettings | None = None


@lru_cache
def get_infra_settings() -> InfraSettings:
    return InfraSettings()


def _settings_file() -> Path:
    return get_infra_settings().settings_file


def load_app_settings() -> AppSettings:
    global _settings_cache
    with _lock:
        if _settings_cache is not None:
            return _settings_cache
        path = _settings_file()
        if path.exists():
            _settings_cache = AppSettings.model_validate_json(path.read_text(encoding="utf-8"))
        else:
            _settings_cache = AppSettings()
        return _settings_cache


def save_app_settings(settings: AppSettings) -> None:
    global _settings_cache
    with _lock:
        path = _settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings.model_dump(), indent=2), encoding="utf-8")
        _settings_cache = settings


def update_app_settings(**changes) -> AppSettings:
    current = load_app_settings()
    updated = current.model_copy(update=changes)
    save_app_settings(updated)
    return updated
