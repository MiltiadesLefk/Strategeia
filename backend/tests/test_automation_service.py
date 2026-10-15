from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.config import AppSettings
from app.data_providers.base import AllProvidersFailedError, QuoteData


class FakeUniverseProvider:
    """One bullish, strong-momentum, high-volume symbol per requested
    ticker — reliably scores as 'potential_setup' for both scanning and
    trade-plan generation, and has no fundamentals/news (kept out of scope
    for this test)."""

    name = "fake"

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        # Steep enough to clear the momentum engine's 5%-over-10-days ROC
        # threshold, with periodic single-day dips (well outside the tail's
        # 10-day lookback window) so RSI isn't pinned at exactly 100 —
        # reliably classifies Bullish/Strong -> 'potential_setup', unlike a
        # plain straight line (verified empirically; a pure linear or
        # gentle-sine fixture both landed as 'watching', not a setup).
        n = 150
        t = np.arange(n)
        closes = 100 + t * 3.0
        closes = closes.astype(float)
        closes[t % 15 == 0] -= 8.0
        return pd.DataFrame(
            {
                "open": closes - 0.3,
                "high": closes + 1.5,
                "low": closes - 1.5,
                "close": closes,
                "volume": [5_000_000.0] * n,
            }
        )

    def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(symbol=symbol, price=547.0, change_pct_24h=1.0, volume=5_000_000.0, avg_volume_20d=1_000_000.0)

    def get_company_overview(self, symbol: str):
        raise AllProvidersFailedError("no overview in this fake")

    def get_financials(self, symbol: str):
        raise AllProvidersFailedError("no financials in this fake")

    def get_news(self, symbol: str, limit: int = 5):
        raise AllProvidersFailedError("no news in this fake")

    def get_earnings_date(self, symbol: str):
        return None

    def get_options_summary(self, symbol: str):
        return None

    def get_insider_activity(self, symbol: str):
        return None

    def get_earnings_history(self, symbol: str, limit: int = 12):
        return []


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _settings(**overrides) -> AppSettings:
    defaults = dict(
        telegram_bot_token="", telegram_chat_id="", auto_execute_trade_plans=False,
        max_concurrent_positions=5, scan_universe_size=3,
    )
    defaults.update(overrides)
    return AppSettings(**defaults)


