from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import AppSettings
from app.data_providers.base import AllProvidersFailedError, QuoteData
from app.llm_providers.null_provider import NullLLMProvider
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.services import automation_service


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


def test_auto_scan_generates_plans_for_setups(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.automation_service.get_default_watchlist", lambda n: ["AAA", "BBB", "CCC"]
    )
    settings = _settings()

    outcome = automation_service.run_auto_scan(settings, FakeUniverseProvider(), NullLLMProvider(), session)

    assert set(outcome.generated) == {"AAA", "BBB", "CCC"}
    assert outcome.no_trade == []


def test_auto_scan_caps_auto_execution_but_still_evaluates_every_symbol(session, monkeypatch):
    """The position-count cap must only gate EXECUTION, not evaluation —
    every symbol still gets a full plan (quality over quantity: we never
    silently skip looking at a name just because the account is full), it's
    only auto-execute that stops once slots run out."""
    monkeypatch.setattr(
        "app.services.automation_service.get_default_watchlist", lambda n: ["AAA", "BBB", "CCC"]
    )
    settings = _settings(max_concurrent_positions=2, auto_execute_trade_plans=True)

    outcome = automation_service.run_auto_scan(settings, FakeUniverseProvider(), NullLLMProvider(), session)

    assert set(outcome.generated) == {"AAA", "BBB", "CCC"}
    records = session.exec(select(TradePlanRecord)).all()
    statuses = [r.status for r in records]
    assert statuses.count("executed") == 2
    assert statuses.count("pending") == 1


def test_auto_scan_skips_symbols_with_an_open_position(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.automation_service.get_default_watchlist", lambda n: ["AAA", "BBB", "CCC"]
    )
    session.add(
        PaperPosition(
            symbol="AAA", direction="long", entry_price=100.0, stop_loss=95.0, tp1=110.0, tp2=120.0, shares=1, status="open"
        )
    )
    session.commit()
    settings = _settings()

    outcome = automation_service.run_auto_scan(settings, FakeUniverseProvider(), NullLLMProvider(), session)

    assert "AAA" not in outcome.generated
    assert set(outcome.generated) == {"BBB", "CCC"}


def test_auto_scan_still_evaluates_every_symbol_when_already_at_the_position_cap(session, monkeypatch):
    """Regression for the old behavior, which returned [] (never even
    generated a plan) once at the cap. Now: still evaluate and propose plans
    for review, just don't auto-execute any of them past the cap."""
    monkeypatch.setattr(
        "app.services.automation_service.get_default_watchlist", lambda n: ["AAA", "BBB", "CCC"]
    )
    for i in range(3):
        session.add(
            PaperPosition(
                symbol=f"OPEN{i}", direction="long", entry_price=100.0, stop_loss=95.0, tp1=110.0, tp2=120.0,
                shares=1, status="open",
            )
        )
    session.commit()
    settings = _settings(max_concurrent_positions=3, auto_execute_trade_plans=True)

    outcome = automation_service.run_auto_scan(settings, FakeUniverseProvider(), NullLLMProvider(), session)

    assert set(outcome.generated) == {"AAA", "BBB", "CCC"}
    records = session.exec(select(TradePlanRecord).where(TradePlanRecord.symbol.in_(["AAA", "BBB", "CCC"]))).all()
    assert all(r.status == "pending" for r in records)
