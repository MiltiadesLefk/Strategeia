from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class MarketSessionResponse(BaseModel):
    """The US session right now, from the backend's one calendar
    (app/markets.py) — so the UI's badge and Execute button can't disagree
    with the engine about holidays or early closes."""

    # "open" | "pre" (a trading day, before 09:30 ET) | "after" (a trading
    # day, after its close) | "closed" (weekend) | "holiday" (a weekday the
    # exchange is shut; see holiday_name).
    state: Literal["open", "pre", "after", "closed", "holiday"]
    is_open: bool
    # The first session open / close still ahead. While open, next_open is
    # the NEXT trading day's open; next_close is always the real close,
    # 1:00 pm ET on an early-close day (next_close_is_early).
    next_open: UtcDatetime
    next_close: UtcDatetime
    next_close_is_early: bool
    holiday_name: str | None = None
    # The server time this was computed for.
    as_of: UtcDatetime
