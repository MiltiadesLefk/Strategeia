from datetime import datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.deps import get_app_settings, get_data_provider, get_session
from app.config import AppSettings
from app.data_providers.base import QuoteData
from app.main import app
from app.portfolio.holdings import max_drawdown_pct
from app.portfolio.models import AccountState, PaperPosition


def test_max_drawdown():
    assert max_drawdown_pct([100, 120, 90, 110]) == pytest.approx(25.0)
    assert max_drawdown_pct([]) == 0.0 and max_drawdown_pct([1, 2, 3]) == 0.0


class Provider:
    def get_quote(self, symbol):
        return QuoteData(symbol=symbol, price=110.0, change_pct_24h=0, volume=1, avg_volume_20d=1)

    def get_ohlcv(self, symbol, period="1y", interval="1d"):
        days = pd.bdate_range("2026-01-01", periods=10)
        return pd.DataFrame({"date": days, "close": [100.0 + i for i in range(10)]})


def test_holdings_endpoint_weights_drift_and_spy():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(AccountState(starting_cash=100_000.0, current_cash=95_000.0))
        s.add(PaperPosition(symbol="AAPL", direction="long", entry_price=100.0, stop_loss=75.0, tp1=0.0, tp2=0.0,
                            shares=50, mode="hold", opened_at=datetime(2026, 1, 5)))
        s.commit()
        app.dependency_overrides[get_session] = lambda: s
        app.dependency_overrides[get_data_provider] = lambda: Provider()
        app.dependency_overrides[get_app_settings] = lambda: AppSettings()
        try:
            body = TestClient(app).get("/api/portfolio/holdings").json()
        finally:
            app.dependency_overrides.clear()
    assert body["equity"] == pytest.approx(95_000 + 5_500)
    row = body["positions"][0]
    assert row["weight_pct"] == pytest.approx(5_500 / 100_500 * 100)
    assert row["drift_pct"] == pytest.approx(row["weight_pct"] - 4.5)
    assert row["pnl"] == pytest.approx(500.0)
    assert body["total_return_pct"] == pytest.approx(0.5)
    assert body["spy_return_pct"] == pytest.approx((109 / 102 - 1) * 100)  # SPY from the entry day's close (Jan 5)
