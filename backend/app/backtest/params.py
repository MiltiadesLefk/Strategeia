"""What defines a backtest run: the request, the settings it may override, and the
effective settings it runs with.

Kept apart from runner.py so the API schemas and the job service can use the same
validated types without importing the day loop.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.config import AppSettings
from app.markets import is_always_on
from app.strategy.snapshot import DECISION_SETTINGS, build_snapshot, fingerprint

MAX_SYMBOLS = 600
MAX_SPAN_DAYS = 366 * 25
MAX_DECISION_EVERY_N_DAYS = 60
BENCHMARK_SYMBOLS = ("SPY", "^VIX")


class BacktestInputError(ValueError):
    """The run cannot start as asked (bad symbols, missing history, bad dates)."""


class SettingsOverrides(BaseModel):
    """The strategy settings a run may override. Anything not listed here is not
    overridable on purpose: the AI, Telegram, data sources and the auto-execute
    switch are fixed by the backtest itself (see effective_settings)."""

    slippage_bps: float | None = Field(default=None, ge=0, le=500)
    commission_per_trade: float | None = Field(default=None, ge=0, le=1000)
    default_risk_pct: float | None = Field(default=None, gt=0, le=100)
    min_confidence_for_trade: int | None = Field(default=None, ge=0, le=100)
    max_concurrent_positions: int | None = Field(default=None, gt=0, le=1000)
    max_positions_per_sector: int | None = Field(default=None, gt=0, le=1000)
    max_position_pct_of_adv: float | None = Field(default=None, gt=0, le=100)
    max_holding_days: int | None = Field(default=None, ge=0, le=60)
    paper_starting_cash: float | None = Field(default=None, gt=0)

    model_config = {"extra": "forbid"}


class BacktestParams(BaseModel):
    symbols: list[str]
    start: date
    end: date
    decision_every_n_days: int = Field(default=1, ge=1, le=MAX_DECISION_EVERY_N_DAYS)
    overrides: SettingsOverrides = Field(default_factory=SettingsOverrides)

    model_config = {"extra": "forbid"}

    @field_validator("symbols")
    @classmethod
    def _clean_symbols(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            symbol = raw.strip().upper()
            if symbol and symbol not in cleaned:
                cleaned.append(symbol)
        if not cleaned:
            raise ValueError("give at least one symbol")
        if len(cleaned) > MAX_SYMBOLS:
            raise ValueError(f"at most {MAX_SYMBOLS} symbols per run")
        crypto = [s for s in cleaned if is_always_on(s)]
        if crypto:
            raise ValueError(f"crypto symbols are not supported by the backtester yet: {', '.join(crypto)}")
        return cleaned

    def validate_dates(self) -> None:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if (self.end - self.start).days > MAX_SPAN_DAYS:
            raise ValueError("a run can cover at most 25 years")


def effective_settings(base: AppSettings, overrides: SettingsOverrides | None = None) -> AppSettings:
    """A copy of `base` with the backtest's fixed choices and the run's
    overrides applied. `base` itself (and the settings file) is never touched.

    Fixed: no LLM and no AI overlay (an AI that has read the future cannot be
    tested honestly), no Telegram, no Finnhub, auto-execute on (the backtest is
    the auto-trade path), no scheduled scan."""
    changes: dict[str, Any] = {
        "llm_provider": "none",
        "ai_trading_overlay_enabled": False,
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "finnhub_enabled": False,
        "finnhub_api_key": "",
        "auto_execute_trade_plans": True,
        "auto_scan_enabled": False,
    }
    if overrides is not None:
        changes.update(overrides.model_dump(exclude_none=True))
    return base.model_copy(update=changes)


def settings_snapshot(settings: AppSettings) -> dict[str, Any]:
    """The values that shaped this run's decisions, for the stored params."""
    snapshot = {name: getattr(settings, name) for name in DECISION_SETTINGS}
    snapshot["paper_starting_cash"] = settings.paper_starting_cash
    return snapshot


def strategy_fingerprint(settings: AppSettings) -> str:
    """The same hash live plans carry as their strategy version (read only: it
    creates no version row anywhere)."""
    return fingerprint(build_snapshot(settings))
