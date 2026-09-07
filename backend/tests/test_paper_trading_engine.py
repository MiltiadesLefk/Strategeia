from __future__ import annotations

import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.data_providers.base import QuoteData
from app.portfolio.engine import PaperTradingEngine
from app.portfolio.models import TradePlanRecord
from app.portfolio.stats import compute_portfolio_stats

STARTING_CASH = 10_000.0


class FakeDataProvider:
    name = "fake"

    def __init__(self, price: float, high: float, low: float):
        self.price = price
        self.high = high
        self.low = low

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        return pd.DataFrame(
            {"open": [self.price], "high": [self.high], "low": [self.low], "close": [self.price], "volume": [1_000_000.0]}
        )

    def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(symbol=symbol, price=self.price, change_pct_24h=0.0, volume=1_000_000.0, avg_volume_20d=1_000_000.0)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def make_plan(session: Session, **overrides) -> TradePlanRecord:
    defaults = dict(
        symbol="AAPL",
        direction="long",
        entry=100.0,
        stop=95.0,
        tp1=110.0,
        tp2=120.0,
        rr1=2.0,
        rr2=4.0,
        suggested_shares=10,
        account_risk_dollars=50.0,
        confidence_score=70,
        ai_take_text="test",
        ai_provider="none",
    )
    defaults.update(overrides)
    plan = TradePlanRecord(**defaults)
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def test_open_position_deducts_cash_for_long(session: Session):
    plan = make_plan(session)
    provider = FakeDataProvider(price=100.0, high=100.0, low=100.0)
    engine = PaperTradingEngine(session, provider, STARTING_CASH)

    position = engine.open_position(plan)

    assert position.status == "open"
    assert position.shares == 10
    account = engine.get_account_state()
    assert account.current_cash == STARTING_CASH - 10 * 100.0

    # Regression: _record_equity_snapshot()'s internal commit used to expire
    # `position`'s attributes after open_position() had already returned it,
    # so model_dump() (used by the API layer) silently came back empty.
    dumped = position.model_dump()
    assert dumped["id"] == position.id
    assert dumped["shares"] == 10


def test_mark_to_market_closes_on_stop_hit(session: Session):
    plan = make_plan(session)
    provider = FakeDataProvider(price=100.0, high=100.0, low=100.0)
    engine = PaperTradingEngine(session, provider, STARTING_CASH)
    engine.open_position(plan)

    provider.low = 94.0  # dips through the 95 stop
    closed = engine.mark_to_market()

    assert len(closed) == 1
    position = closed[0]
    assert position.status == "closed"
    assert position.close_reason == "stop_hit"
    assert position.close_price == 95.0
    assert position.realized_pnl == -50.0  # (95-100)*10
    assert position.realized_r == -1.0

    account = engine.get_account_state()
    assert account.current_cash == STARTING_CASH - 10 * 100.0 + 10 * 95.0


def test_mark_to_market_closes_on_tp1_hit(session: Session):
    plan = make_plan(session)
    provider = FakeDataProvider(price=100.0, high=100.0, low=100.0)
    engine = PaperTradingEngine(session, provider, STARTING_CASH)
    engine.open_position(plan)

    provider.high = 111.0  # pokes through the 110 tp1
    closed = engine.mark_to_market()

    assert len(closed) == 1
    position = closed[0]
    assert position.close_reason == "tp1_hit"
    assert position.close_price == 110.0
    assert position.realized_pnl == 100.0  # (110-100)*10
    assert position.realized_r == 2.0


def test_portfolio_stats_reflect_closed_trades(session: Session):
    plan = make_plan(session)
    provider = FakeDataProvider(price=100.0, high=100.0, low=100.0)
    engine = PaperTradingEngine(session, provider, STARTING_CASH)
    engine.open_position(plan)
    provider.high = 111.0
    engine.mark_to_market()

    stats = compute_portfolio_stats(session, provider, STARTING_CASH)

    assert stats.total_trades == 1
    assert stats.win_rate == 100.0
    assert stats.avg_rr == 2.0
    assert stats.active_positions == 0
    expected_cash = STARTING_CASH - 10 * 100.0 + 10 * 110.0
    assert stats.portfolio_value == expected_cash
    assert stats.total_return == pytest.approx((expected_cash - STARTING_CASH) / STARTING_CASH * 100)


def test_portfolio_stats_with_no_trades_yet(session: Session):
    provider = FakeDataProvider(price=100.0, high=100.0, low=100.0)
    stats = compute_portfolio_stats(session, provider, STARTING_CASH)

    assert stats.total_trades == 0
    assert stats.win_rate == 0.0
    assert stats.avg_rr is None
    assert stats.total_return == 0.0
