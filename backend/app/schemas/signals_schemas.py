from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.trade_plan_schemas import ShadowSignalOut


class FinraRefreshRequest(BaseModel):
    # Omitted: the symbols currently scanned (the watchlist, capped at the scan size).
    symbols: list[str] | None = Field(default=None, max_length=200)


class FinraRefreshResponse(BaseModel):
    symbols: int
    days_checked: int
    days_fetched: int
    days_without_file: int
    facts_created: int
    facts_existing: int
    errors: list[str] = []


class FinraDay(BaseModel):
    trade_date: str
    short_volume: float
    total_volume: float
    ratio: float


class FinraSymbolResponse(BaseModel):
    symbol: str
    stored_days: int
    recent_ratio: float | None = None
    recent_days: int = 0
    baseline_ratio: float | None = None
    baseline_days: int = 0
    latest_trade_date: str | None = None
    days: list[FinraDay] = []
    # What the silent signal reads right now for the optional ?direction=.
    signal: ShadowSignalOut | None = None
    note: str = (
        "Short-sale volume is not short interest: much of it is market makers providing liquidity. "
        "This signal is recorded on plans but does not change confidence."
    )
