"""Regression tests for the exit engine's realism fixes.

Every case here passed silently before: the old mark_to_market read only
`bars.iloc[-1]` and filled exactly at the stop, so a position could trade
clean through its stop, recover, and stay open at a paper profit. That error
only ever deleted losses, which inflated win rate and total return together —
exactly the kind of flattery this project's stats are supposed to be free of.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.data_providers.base import QuoteData
from app.portfolio.engine import (
    MAX_ENTRY_DRIFT_PCT,
    PaperTradingEngine,
    SectorConcentrationError,
    StalePlanError,
)
from app.portfolio.models import EquitySnapshot, PaperPosition, TradePlanRecord
from app.timeutil import utcnow_naive

STARTING_CASH = 100_000.0


class BarsProvider:
    """Returns a caller-supplied OHLCV frame with real dates, so the engine's
    "which bars postdate entry" logic is actually exercised."""

    name = "fake"

    def __init__(self, bars: list[dict], price: float = 100.0, avg_volume: float = 1_000_000.0):
        self._bars = bars
        self.price = price
        self.avg_volume = avg_volume

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        return pd.DataFrame(self._bars)

    def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(
            symbol=symbol, price=self.price, change_pct_24h=0.0, volume=1_000.0, avg_volume_20d=self.avg_volume
        )


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
    )
    defaults.update(overrides)
    plan = TradePlanRecord(**defaults)
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def bars_from(opened_at, rows: list[tuple[float, float, float, float]]) -> list[dict]:
    """rows are (open, high, low, close), one per day, starting the day AFTER
    the entry bar. Index 0 is the entry bar itself so the engine has something
    to anchor "entry happened here" to."""
    out = []
    for offset, (o, h, low, c) in enumerate(rows):
        out.append(
            {
                "date": opened_at + timedelta(days=offset),
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": 1_000_000.0,
            }
        )
    return out


def build_engine(session, provider, **kwargs) -> PaperTradingEngine:
    return PaperTradingEngine(session, provider, STARTING_CASH, **kwargs)


# --------------------------------------------------------------- the main bug


def test_stop_hit_on_an_intermediate_bar_is_not_missed(session: Session):
    """THE regression. Day 3 gapped straight through the stop and recovered;
    the latest bar never touches it. Reading only the last bar left this
    position open at a paper profit after a -20% excursion."""
    opened = utcnow_naive() - timedelta(days=5)
    rows = [
        (100, 101, 99, 100),  # entry bar — spent, not scanned
        (100, 101, 98, 99),
        (97, 97, 80, 82),  # blows through the 95 stop
        (83, 94, 83, 93),
        (96, 99, 96, 98),  # latest bar: never trades below 96
    ]
    provider = BarsProvider(bars_from(opened, rows), price=98.0)
    engine = build_engine(session, provider)
    plan = make_plan(session)
    position = engine.open_position(plan)
    position.opened_at = opened
    session.add(position)
    session.commit()

    closed = engine.mark_to_market()

    assert len(closed) == 1
    assert closed[0].close_reason == "stop_hit"
    assert closed[0].realized_pnl < 0


def test_stop_fill_uses_the_gap_open_not_the_stop_price(session: Session):
    """A stop is a market order triggered at the level, not a fill at it. The
    bar opens at 83 with a 95 stop, so the fill is ~83 — the 12-point
    difference is the loss the old engine silently erased."""
    opened = utcnow_naive() - timedelta(days=2)
    rows = [(100, 101, 99, 100), (83, 90, 80, 88)]
    provider = BarsProvider(bars_from(opened, rows), price=100.0)
    engine = build_engine(session, provider)
    plan = make_plan(session)
    position = engine.open_position(plan)
    position.opened_at = opened
    session.add(position)
    session.commit()

    closed = engine.mark_to_market()

    assert len(closed) == 1
    assert closed[0].close_price == pytest.approx(83.0)
    assert closed[0].close_price < 95.0  # strictly worse than the stop


def test_take_profit_gap_fills_at_the_better_open(session: Session):
    """The mirror image: a resting limit filled by a gap UP fills at the open,
    which is better than the limit. Realism has to cut both ways or it's just
    a different bias."""
    opened = utcnow_naive() - timedelta(days=2)
    rows = [(100, 101, 99, 100), (115, 118, 114, 117)]
    provider = BarsProvider(bars_from(opened, rows), price=100.0)
    engine = build_engine(session, provider)
    plan = make_plan(session)
    position = engine.open_position(plan)
    position.opened_at = opened
    session.add(position)
    session.commit()

    closed = engine.mark_to_market()

    assert len(closed) == 1
    assert closed[0].close_reason == "tp1_hit"
    assert closed[0].close_price == pytest.approx(115.0)  # the open, not tp1=110


def test_entry_bar_cannot_trigger_its_own_stop(session: Session):
    """Entry is a daily CLOSE, so the entry bar is already spent. Its
    pre-entry low must not stop out a position that did not exist yet."""
    opened = utcnow_naive()
    rows = [(100, 101, 80, 100)]  # entry bar dipped to 80, below the 95 stop
    provider = BarsProvider(bars_from(opened, rows), price=100.0)
    engine = build_engine(session, provider)
    plan = make_plan(session)
    engine.open_position(plan)

    assert engine.mark_to_market() == []


def test_stop_checked_before_target_within_one_bar(session: Session):
    """When both levels sit inside one bar, daily OHLC cannot order them, so
    the engine must take the unfavourable branch rather than the flattering
    one."""
    opened = utcnow_naive() - timedelta(days=2)
    rows = [(100, 101, 99, 100), (100, 115, 90, 105)]  # touches both 95 and 110
    provider = BarsProvider(bars_from(opened, rows), price=100.0)
    engine = build_engine(session, provider)
    plan = make_plan(session)
    position = engine.open_position(plan)
    position.opened_at = opened
    session.add(position)
    session.commit()

    closed = engine.mark_to_market()

    assert closed[0].close_reason == "stop_hit"


def test_slippage_worsens_a_stop_but_never_a_target(session: Session):
    opened = utcnow_naive() - timedelta(days=2)
    stop_rows = [(100, 101, 99, 100), (100, 100, 90, 92)]
    provider = BarsProvider(bars_from(opened, stop_rows), price=100.0)
    engine = build_engine(session, provider, slippage_bps=50.0)  # 0.5%
    plan = make_plan(session)
    position = engine.open_position(plan)
    position.opened_at = opened
    session.add(position)
    session.commit()

    closed = engine.mark_to_market()
    assert closed[0].close_price < 95.0  # stop, slipped against the account

    tp_rows = [(100, 101, 99, 100), (100, 112, 100, 111)]
    provider2 = BarsProvider(bars_from(opened, tp_rows), price=100.0)
    engine2 = build_engine(session, provider2, slippage_bps=50.0)
    plan2 = make_plan(session, symbol="MSFT")
    position2 = engine2.open_position(plan2)
    position2.opened_at = opened
    session.add(position2)
    session.commit()

    closed2 = engine2.mark_to_market()
    assert closed2[0].close_price == pytest.approx(110.0)  # limit, untouched by slippage


# ------------------------------------------------------------- equity curve


def test_read_only_marking_does_not_append_to_the_equity_curve(session: Session):
    """GET /positions and /stats mark but must not write. With
    refetch-on-focus, a snapshot per call made the curve a record of how often
    the dashboard was looked at."""
    provider = BarsProvider(bars_from(utcnow_naive(), [(100, 101, 99, 100)]), price=100.0)
    engine = build_engine(session, provider)
    engine.get_account_state()

    before = len(session.exec(select(EquitySnapshot)).all())
    for _ in range(5):
        engine.mark_to_market(snapshot=False)
    after = len(session.exec(select(EquitySnapshot)).all())

    assert before == after


def test_marking_writes_exactly_one_snapshot_per_sweep(session: Session):
    """Snapshotting inside close_position meant closing k positions re-quoted
    every open position k times and wrote k rows."""
    opened = utcnow_naive() - timedelta(days=2)
    rows = [(100, 101, 99, 100), (100, 100, 90, 92)]
    provider = BarsProvider(bars_from(opened, rows), price=100.0)
    engine = build_engine(session, provider)
    for symbol in ("AAPL", "MSFT"):
        plan = make_plan(session, symbol=symbol)
        position = engine.open_position(plan)
        position.opened_at = opened
        session.add(position)
        session.commit()

    before = len(session.exec(select(EquitySnapshot)).all())
    closed = engine.mark_to_market()

    assert len(closed) == 2
    assert len(session.exec(select(EquitySnapshot)).all()) - before == 1


# ---------------------------------------------------------- open-time guards


def test_stale_plan_is_refused(session: Session):
    """A plan's stop/size/RR all derive from its entry price. Filling one built
    days ago at a price the market has left behind keeps the UI's numbers
    while silently changing the trade they describe."""
    drifted = 100.0 * (1 + MAX_ENTRY_DRIFT_PCT + 0.01)
    provider = BarsProvider(bars_from(utcnow_naive(), [(100, 101, 99, 100)]), price=drifted)
    engine = build_engine(session, provider)
    plan = make_plan(session)

    with pytest.raises(StalePlanError):
        engine.open_position(plan)


def test_entry_fills_at_current_market_within_drift(session: Session):
    provider = BarsProvider(bars_from(utcnow_naive(), [(100, 101, 99, 100)]), price=101.0)
    engine = build_engine(session, provider)
    plan = make_plan(session)

    position = engine.open_position(plan)

    assert position.entry_price == pytest.approx(101.0)
    assert position.planned_entry_price == pytest.approx(100.0)


def test_sector_concentration_cap_blocks_a_third_tech_name(session: Session):
    provider = BarsProvider(bars_from(utcnow_naive(), [(100, 101, 99, 100)]), price=100.0)
    engine = build_engine(session, provider, max_positions_per_sector=2)
    for symbol in ("AAPL", "MSFT"):
        engine.open_position(make_plan(session, symbol=symbol))

    with pytest.raises(SectorConcentrationError):
        engine.open_position(make_plan(session, symbol="NVDA"))


def test_position_size_capped_to_a_fraction_of_average_volume(session: Session):
    """A paper fill is infinitely liquid; a real one is not."""
    provider = BarsProvider(bars_from(utcnow_naive(), [(100, 101, 99, 100)]), price=100.0, avg_volume=500.0)
    engine = build_engine(session, provider, max_position_pct_of_adv=1.0)  # 1% of 500 = 5 shares
    plan = make_plan(session, suggested_shares=10)

    position = engine.open_position(plan)

    assert position.shares == 5


def test_commission_is_charged_on_both_legs_and_nets_out_of_pnl(session: Session):
    opened = utcnow_naive() - timedelta(days=2)
    rows = [(100, 101, 99, 100), (100, 112, 100, 111)]
    provider = BarsProvider(bars_from(opened, rows), price=100.0)
    engine = build_engine(session, provider, commission_per_trade=1.0)
    plan = make_plan(session)
    position = engine.open_position(plan)
    position.opened_at = opened
    session.add(position)
    session.commit()

    closed = engine.mark_to_market()

    gross = (closed[0].close_price - closed[0].entry_price) * closed[0].shares
    assert closed[0].fees_paid == pytest.approx(2.0)
    assert closed[0].realized_pnl == pytest.approx(gross - 2.0)
