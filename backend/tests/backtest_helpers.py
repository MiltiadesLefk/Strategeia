"""Hand-built price series for the backtester tests (no network, no files)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.backtest.data_provider import PriceBook
from app.markets import is_us_trading_day


def trading_days_from(start: date, count: int) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if is_us_trading_day(day):
            days.append(day)
        day += timedelta(days=1)
    return days


def frame_from(
    days: list[date],
    closes,
    *,
    opens=None,
    highs=None,
    lows=None,
    volume: float = 1_000_000.0,
) -> pd.DataFrame:
    """Daily bars. Unless given: open = previous close, high/low = 0.3% beyond the open/close."""
    closes = np.asarray(closes, dtype=float)
    if opens is None:
        opens = np.r_[closes[0], closes[:-1]]
    opens = np.asarray(opens, dtype=float)
    if highs is None:
        highs = np.maximum(opens, closes) * 1.003
    if lows is None:
        lows = np.minimum(opens, closes) * 0.997
    return pd.DataFrame(
        {
            "date": pd.to_datetime(days),
            "open": opens,
            "high": np.asarray(highs, dtype=float),
            "low": np.asarray(lows, dtype=float),
            "close": closes,
            "volume": np.full(len(closes), volume),
        }
    )


def wiggly_uptrend(count: int, start_price: float = 100.0, daily: float = 0.006, dip_every: int = 7, dip: float = -0.012):
    """Steady climb with a small dip every few days (keeps RSI off the 100 ceiling)."""
    returns = np.full(count, daily)
    returns[np.arange(count) % dip_every == dip_every - 1] = dip
    return start_price * np.cumprod(1 + returns)


def flat_series(count: int, price: float) -> np.ndarray:
    return np.full(count, price)


def standard_book(days: list[date], symbol_closes: dict[str, np.ndarray], *, spy=None, vix_level: float = 15.0) -> PriceBook:
    """The symbols plus the two benchmarks the strategy reads (SPY trending up, VIX calm)."""
    count = len(days)
    spy_closes = wiggly_uptrend(count, 400.0, 0.002) if spy is None else spy
    frames = {symbol: frame_from(days, closes) for symbol, closes in symbol_closes.items()}
    frames["SPY"] = frame_from(days, spy_closes)
    frames["^VIX"] = frame_from(days, flat_series(count, vix_level))
    return PriceBook.from_frames(frames)
