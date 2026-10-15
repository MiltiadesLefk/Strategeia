"""Hold buy (PORTOFOLIO_BUILDER.md step 2): target-weight size, wide stop, engine checks still apply."""
from __future__ import annotations

import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import AppSettings
from app.data_providers.base import QuoteData
from app.portfolio.engine import DuplicatePositionError, InsufficientCashError, PaperTradingEngine
from app.portfolio.models import PaperPosition
from app.services.hold_buy_service import AlreadyAtTargetError, hold_buy, hold_shares, hold_stop_level


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
    monkeypatch.setattr(hold_buy_service, "check_quality", lambda dp, symbol, **kw: GateResult(symbol != "BAD", []))
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
    monkeypatch.setattr(hold_buy_service, "check_quality", lambda dp, symbol, **kw: GateResult(True, [], dip=symbol == "BBB"))
    assert automation_service.run_hold_scan(AppSettings(hold_buy_only_dips=True), Provider(), session) == ["BBB"]


def test_add_averages_the_cost_and_stops_at_target(session):
    from app.services.hold_buy_service import hold_add

    provider, settings = Provider(), AppSettings(hold_target_weight_pct=5.0)
    engine = PaperTradingEngine(session, provider, 100_000.0)
    p = hold_buy(session, engine, provider, AppSettings(hold_target_weight_pct=2.5), "NOVA")  # 25 shares at 100
    assert p.shares == 25
    provider.price = 110.0
    p = hold_add(session, engine, provider, settings, p)
    assert p.shares > 25 and p.entry_price == pytest.approx((25 * 100.0 + (p.shares - 25) * 110.0) / p.shares, rel=1e-3)
    assert p.stop_loss == pytest.approx(75.0)
    with pytest.raises(AlreadyAtTargetError):  # now at target
        hold_add(session, engine, provider, settings, p)


def test_hold_scan_adds_to_a_held_dip_only_when_enabled(session, monkeypatch):
    from app.analysis.quality_gate import GateResult
    from app.services import automation_service, hold_buy_service

    provider = Provider()
    engine = PaperTradingEngine(session, provider, 100_000.0)
    hold_buy(session, engine, provider, AppSettings(hold_target_weight_pct=2.5), "NOVA")  # 25 shares
    monkeypatch.setattr(automation_service, "get_default_watchlist", lambda n: [])
    monkeypatch.setattr(automation_service, "notify", lambda *a, **k: None)
    monkeypatch.setattr(hold_buy_service, "check_quality", lambda dp, symbol, **kw: GateResult(True, [], dip=True))
    automation_service.run_hold_scan(AppSettings(), provider, session)
    assert session.exec(select(PaperPosition)).one().shares == 25  # off by default
    automation_service.run_hold_scan(AppSettings(hold_add_on_dips=True), provider, session)
    assert session.exec(select(PaperPosition)).one().shares == 45


def test_hold_scan_buys_a_news_discovered_ticker_outside_the_universe(session, monkeypatch):
    from app.analysis.quality_gate import GateResult
    from app.data_providers.base import NewsItem
    from app.portfolio.models import TradePlanRecord
    from app.services import automation_service, hold_buy_service

    class NewsProvider(Provider):
        def get_news(self, symbol, limit=5):
            return [NewsItem("Vistra (NYSE: VST) and $FAKE and $AAA rally", "x", "u", "now")]

        def get_quote(self, symbol):
            if symbol == "FAKE":
                raise ValueError("no such symbol")
            return super().get_quote(symbol)

    monkeypatch.setattr(automation_service, "get_default_watchlist", lambda n: ["AAA"])
    monkeypatch.setattr("app.services.news_discovery_service.get_default_watchlist", lambda n: ["AAA"])
    monkeypatch.setattr("app.services.news_discovery_service.load_universe", lambda: [type("E", (), {"symbol": "AAA"})()])
    monkeypatch.setattr(automation_service, "notify", lambda *a, **k: None)
    monkeypatch.setattr(hold_buy_service, "check_quality", lambda dp, symbol, **kw: GateResult(True, []))
    bought = automation_service.run_hold_scan(AppSettings(news_discovery_enabled=True), NewsProvider(), session)
    assert bought == ["AAA", "VST"]  # FAKE has no quote, AAA is already in the universe
    plan = session.exec(select(TradePlanRecord).where(TradePlanRecord.symbol == "VST")).one()
    assert "NYSE: VST" in plan.reason


def test_check_quality_archives_the_fundamentals_it_fetched(session):
    from app.data_providers.base import CompanyOverview
    from app.knowledge import FactKind, facts_known_as_of
    from app.services.hold_buy_service import check_quality

    class WithFundamentals(Provider):
        def get_company_overview(self, symbol):
            return CompanyOverview(symbol=symbol, name="Nova", market_cap=1e9, pe_ratio=20.0, revenue_ttm=1e8,
                                   eps_ttm=2.0, week52_low=70.0, week52_high=120.0)

        def get_financials(self, symbol):
            raise RuntimeError("no history")  # the gate treats this as no years; the overview alone is archived

    check_quality(WithFundamentals(), "NOVA", session=session)
    assert facts_known_as_of(session, FactKind.FUNDAMENTALS_SNAPSHOT, symbol="NOVA", limit=1)
