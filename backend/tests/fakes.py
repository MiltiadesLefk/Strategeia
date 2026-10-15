"""Shared fakes for tests (kept from the retired swing trade-plan tests)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.data_providers.base import AllProvidersFailedError, QuoteData
from app.llm_providers.base import LLMResult


class FakeConfiguredLLMProvider:
    """A stand-in for a real, configured LLM provider (unlike
    NullLLMProvider, whose `name == "none"` makes it deliberately excluded
    from the AI features). Records every prompt it's asked to answer
    so tests can assert on call counts."""

    name = "fake-llm"

    def __init__(self, response_text: str = '{"stance": "bullish", "confidence": 80, "reasoning": "Strong setup."}'):
        self.response_text = response_text
        self.prompts: list[str] = []
        self.tiers: list[str] = []  # the tier each call asked for, in order

    def is_configured(self) -> bool:
        return True

    def generate(
        self, prompt: str, *, max_tokens: int = 300, temperature: float = 0.4, tier: str = "routine"
    ) -> LLMResult:
        self.prompts.append(prompt)
        self.tiers.append(tier)
        return LLMResult(text=self.response_text, provider=self.name, latency_ms=1)


class FakeUptrendDataProvider:
    """A steep, high-volume uptrend with periodic single-day dips (keeps RSI
    off the 100 ceiling, unlike a pure straight line) — reliably classifies
    Bullish/Strong momentum with a high
    score. Fundamentals/news are deliberately unavailable (AllProvidersFailedError / None)."""

    name = "fake"

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
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


class FakeFlatDataProvider:
    """Dead-flat price series — analyze_chart classifies this Neutral (see
    test_trend.py's equivalent), so a trade plan must take the
    no-trade path rather than forcing a directional plan with no real trend
    to size it against."""

    name = "fake"

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        n = 150
        closes = [100.0] * n
        return pd.DataFrame(
            {
                "open": closes,
                "high": [c + 0.1 for c in closes],
                "low": [c - 0.1 for c in closes],
                "close": closes,
                "volume": [1_000_000.0] * n,
            }
        )

    def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(symbol=symbol, price=100.0, change_pct_24h=0.0, volume=1_000_000.0, avg_volume_20d=1_000_000.0)

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
