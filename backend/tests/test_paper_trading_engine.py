from __future__ import annotations

import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.data_providers.base import QuoteData
from app.portfolio.engine import (
    DuplicatePositionError,
    InsufficientCashError,
    MaxPositionsExceededError,
    PaperTradingEngine,
)
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


def test_open_position_raises_when_cash_cant_afford_one_share(session: Session):
    """Regression: opening used to silently floor to 0 shares and create a
    phantom position — a real trade that shows up as 'open' but represents
    no actual exposure. That's the bug behind trades users couldn't
    meaningfully see: cash mutated by $0, nothing to track."""
    plan = make_plan(session, entry=100.0, suggested_shares=10)
    provider = FakeDataProvider(price=100.0, high=100.0, low=100.0)
    engine = PaperTradingEngine(session, provider, 50.0)  # can't afford even 1 share at $100

    with pytest.raises(InsufficientCashError):
        engine.open_position(plan)

    account = engine.get_account_state()
    assert account.current_cash == 50.0  # untouched, no phantom position created


def test_open_position_raises_for_zero_suggested_shares(session: Session):
    plan = make_plan(session, direction="short", suggested_shares=0)
    provider = FakeDataProvider(price=100.0, high=100.0, low=100.0)
    engine = PaperTradingEngine(session, provider, STARTING_CASH)

    with pytest.raises(InsufficientCashError):
        engine.open_position(plan)


def test_open_position_raises_for_symbol_that_already_has_an_open_position(session: Session):
    """Regression: nothing stopped a second 'Generate Trade Plan' + execute
    (manual or auto) for a symbol that already had an open position, silently
    pyramiding into it — the v1 exit rule (mark_to_market closes a position
    fully, all-or-nothing) has no concept of which fill a stop/TP hit
    belongs to once there are two."""
    first_plan = make_plan(session)
    provider = FakeDataProvider(price=100.0, high=100.0, low=100.0)
    engine = PaperTradingEngine(session, provider, STARTING_CASH)
    engine.open_position(first_plan)

    second_plan = make_plan(session, entry=101.0, stop=96.0)
    with pytest.raises(DuplicatePositionError):
        engine.open_position(second_plan)

    account = engine.get_account_state()
    assert account.current_cash == STARTING_CASH - 10 * 100.0  # unchanged by the rejected second open


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


def test_open_short_position_unrealized_value_when_price_unchanged(session: Session):
    """Regression: portfolio_value used to sum shares*price for every open
    position regardless of direction, double-counting a short's notional —
    opening a short adds shares*entry to cash immediately (proceeds from the
    sale), then summing +shares*price again inflated portfolio_value/
    total_return dramatically. With price unchanged since entry, an open
    short should contribute exactly $0 of *unrealized* value beyond the cash
    already credited at open."""
    plan = make_plan(session, direction="short", entry=100.0, stop=105.0, suggested_shares=10)
    provider = FakeDataProvider(price=100.0, high=100.0, low=100.0)
    engine = PaperTradingEngine(session, provider, STARTING_CASH)
    engine.open_position(plan)

    stats = compute_portfolio_stats(session, provider, STARTING_CASH)

    assert stats.portfolio_value == pytest.approx(STARTING_CASH)
    assert stats.total_return == pytest.approx(0.0)


def test_open_short_position_gains_value_as_price_falls(session: Session):
    plan = make_plan(session, direction="short", entry=100.0, stop=105.0, suggested_shares=10)
    provider = FakeDataProvider(price=100.0, high=100.0, low=100.0)
    engine = PaperTradingEngine(session, provider, STARTING_CASH)
    engine.open_position(plan)

    provider.price = 90.0  # price fell 10 -> short is up $10/share unrealized
    stats = compute_portfolio_stats(session, provider, STARTING_CASH)

    assert stats.portfolio_value == pytest.approx(STARTING_CASH + 100.0)


def test_open_short_position_raises_when_cash_cant_afford_it(session: Session):
    """Regression: the affordability cap in open_position() used to only
    run `if direction == 'long'` — a short's suggested_shares flowed
    straight through with no cash/collateral check at all, letting the
    account take on short exposure with no bound relative to real capital."""
    plan = make_plan(session, direction="short", entry=100.0, stop=105.0, suggested_shares=1000)
    provider = FakeDataProvider(price=100.0, high=100.0, low=100.0)
    engine = PaperTradingEngine(session, provider, STARTING_CASH)  # 10,000 cash; 1000 shares @ $100 = $100,000 notional

    position = engine.open_position(plan)

    assert position.shares == 100  # capped to STARTING_CASH / entry, not the full 1000 requested


def test_short_proceeds_are_not_spendable_on_a_new_position(session: Session):
    """Regression: opening a short credits its sale proceeds to current_cash
    immediately (real brokers do too) — but until the short is closed,
    that's collateral, not free cash. Before this fix, only the long-side
    affordability check existed, so a short's proceeds could be spent again
    sizing a brand-new position, letting the account chain trades into
    aggregate exposure with no real bound relative to its actual starting
    capital."""
    short_plan = make_plan(session, symbol="TSLA", direction="short", entry=100.0, stop=105.0, suggested_shares=50)
    provider = FakeDataProvider(price=100.0, high=100.0, low=100.0)
    engine = PaperTradingEngine(session, provider, STARTING_CASH)
    engine.open_position(short_plan)

    account = engine.get_account_state()
    assert account.current_cash == STARTING_CASH + 50 * 100.0  # 15,000 — proceeds credited, as designed

    long_plan = make_plan(session, symbol="AAPL", direction="long", entry=100.0, stop=95.0, suggested_shares=120)
    long_position = engine.open_position(long_plan)

    # Capped at 100 shares (STARTING_CASH / entry) — not 120 (the full
    # request) and not 150 (floor(current_cash / entry), which is what the
    # short's "phantom" proceeds would wrongly allow).
    assert long_position.shares == 100


def test_open_position_raises_when_max_concurrent_positions_reached(session: Session):
    """Regression: max_concurrent_positions was only enforced by the
    unattended auto-scan loop (automation_service.py) — manually generating
    a trade plan with auto-execute on, or opening a position directly via
    the API, had no cap at all."""
    provider = FakeDataProvider(price=100.0, high=100.0, low=100.0)
    engine = PaperTradingEngine(session, provider, STARTING_CASH, max_concurrent_positions=1)

    first_plan = make_plan(session, symbol="AAPL")
    engine.open_position(first_plan)

    second_plan = make_plan(session, symbol="MSFT", entry=50.0, stop=45.0, suggested_shares=5)
    with pytest.raises(MaxPositionsExceededError):
        engine.open_position(second_plan)

    account = engine.get_account_state()
    assert account.current_cash == STARTING_CASH - 10 * 100.0  # unchanged by the rejected second open
