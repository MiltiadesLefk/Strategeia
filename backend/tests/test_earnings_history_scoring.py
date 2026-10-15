"""Earnings surprise track record and historical earnings-day move.

Both are backward-looking fact used forward: a beat/miss pattern is real
history, not a forecast of the next print, and the historical move % is
computed purely from past OHLCV with no guessing about the future at all.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from app.data_providers.base import EarningsHistoryEntry


def _entry(surprise_pct: float | None, days_ago: int = 90) -> EarningsHistoryEntry:
    return EarningsHistoryEntry(
        date=date.today() - timedelta(days=days_ago),
        eps_estimate=1.0,
        eps_actual=1.0 + (surprise_pct or 0) / 100,
        surprise_pct=surprise_pct,
    )


# --------------------------------------------------------- surprise track record


# --------------------------------------------------------- historical move %


def _ohlcv(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": pd.Timestamp(d, tz="America/New_York"), "open": c, "high": c, "low": c, "close": c, "volume": 1.0}
         for d, c in rows]
    )


