"""Hold buy (PORTOFOLIO_BUILDER.md step 2): target-weight size, wide stop, engine checks still apply."""
from __future__ import annotations

import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.config import AppSettings
from app.data_providers.base import QuoteData
from app.portfolio.engine import DuplicatePositionError, InsufficientCashError, PaperTradingEngine
from app.services.hold_buy_service import hold_buy, hold_shares, hold_stop_level


class Provider:
    name = "fake"

    def __init__(self, price=100.0, bars=250):
        self.price, self.n = price, bars

    def get_quote(self, symbol):
        return QuoteData(symbol=symbol, price=self.price, change_pct_24h=0.0, volume=1.0, avg_volume_20d=10_000_000.0)

    def get_ohlcv(self, symbol, period="1y", interval="1d"):
        return pd.DataFrame({"close": [80.0] * self.n})  # a 200-day average of 80


@pytest.fixture
def session():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(e)
    with Session(e) as s:
        yield s


def test_pure_rules():
    assert hold_stop_level(100, None, 25) == 75
    assert hold_stop_level(100, 60, 25) == 60  # the 200-day is lower: wider wins
    assert hold_stop_level(100, 90, 25) == 75
    assert hold_stop_level(100, 60, 0) == 0.0
    assert hold_shares(100_000, 4.5, 82.40) == 54


def test_buys_target_weight_in_hold_mode(session):
    provider, settings = Provider(), AppSettings()
    engine = PaperTradingEngine(session, provider, 100_000.0)
    p = hold_buy(session, engine, provider, settings, "NOVA")
    assert (p.mode, p.shares, p.tp1, p.tp2) == ("hold", 45, 0.0, 0.0)  # 4,500 / 100
    assert p.stop_loss == pytest.approx(75.0)  # entry 100 -25%; the 200-day (80) is higher, so not the wider one
    with pytest.raises(DuplicatePositionError):
        hold_buy(session, engine, provider, settings, "NOVA")


def test_too_small_account_raises(session):
    provider = Provider(price=10_000.0)
    engine = PaperTradingEngine(session, provider, 100_000.0)
    with pytest.raises(InsufficientCashError):
        hold_buy(session, engine, provider, AppSettings(), "BIG")


# ------------------------------------------------------------ the daily hold scan


def test_hold_scan_buys_passes_only_and_respects_the_per_scan_cap(session, monkeypatch):
    from app.analysis.quality_gate import GateResult
    from app.services import automation_service, hold_buy_service

    provider = Provider()
    monkeypatch.setattr(automation_service, "get_default_watchlist", lambda n: ["AAA", "BAD", "BBB", "CCC"])
    monkeypatch.setattr(automation_service, "notify", lambda *a, **k: None)
    monkeypatch.setattr(hold_buy_service, "check_quality", lambda dp, symbol: GateResult(symbol != "BAD", []))
    settings = AppSettings(hold_max_new_per_scan=2)

    bought = automation_service.run_hold_scan(settings, provider, session)

    assert bought == ["AAA", "BBB"]  # BAD failed the gate, CCC waits for the next scan


def test_one_daily_scan_job():
    from app.scheduler import AUTO_SCAN_SESSION_TIMES_ET

    assert AUTO_SCAN_SESSION_TIMES_ET == [("open", 10, 0)]


def test_hold_scan_can_buy_only_dips(session, monkeypatch):
    from app.analysis.quality_gate import GateResult
    from app.services import automation_service, hold_buy_service

    monkeypatch.setattr(automation_service, "get_default_watchlist", lambda n: ["AAA", "BBB"])
    monkeypatch.setattr(automation_service, "notify", lambda *a, **k: None)
    monkeypatch.setattr(hold_buy_service, "check_quality", lambda dp, symbol: GateResult(True, [], dip=symbol == "BBB"))
    assert automation_service.run_hold_scan(AppSettings(hold_buy_only_dips=True), Provider(), session) == ["BBB"]
