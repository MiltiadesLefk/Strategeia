from __future__ import annotations

import json
import threading
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class InfraSettings(BaseSettings):
    """Static, process-lifetime config from .env — never editable at runtime."""

    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://localhost:5173"
    # Deliberately separate from data/ (which holds the bundled, read-only
    # sp500.csv baked into the Docker image) so a single volume mount at
    # runtime/ can persist the db + settings without hiding sp500.csv.
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

    scan_universe_size: int = 50

    paper_starting_cash: float = 100_000.0
    default_risk_pct: float = 1.0
    mark_to_market_interval_minutes: int = 15

    def redacted(self) -> dict:
        """Copy safe to return over the API — keys collapsed to a presence flag."""
        data = self.model_dump()
        for key in ("openrouter_api_key", "orcarouter_api_key", "openai_api_key", "gemini_api_key", "finnhub_api_key"):
            data[key] = bool(data[key])
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
