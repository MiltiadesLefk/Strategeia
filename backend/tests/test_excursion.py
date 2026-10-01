"""Best and worst price during a trade (MFE / MAE).

Numbers here are worked out by hand from the bars in each test. The rules that
matter: only bars the position was open for count (entry bar excluded), the bar
it EXITED on contributes only what is certain (its open and the exit fill, never
the extreme that may have happened after it left), R is in multiples of the
initial risk, and nothing is ever guessed (no bars gives None).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_app_settings, get_data_provider, get_session
from app.config import AppSettings
from app.data_providers.base import AllProvidersFailedError, QuoteData
from app.main import app
from app.portfolio.engine import PaperTradingEngine
from app.portfolio.excursion import compute_excursion
from app.portfolio.excursion_service import backfill_excursions, live_excursion
from app.portfolio.excursion_stats import compute_excursion_stats
from app.portfolio.models import AccountState, PaperPosition, TradePlanRecord
from app.portfolio.stats import compute_portfolio_stats

STARTING_CASH = 100_000.0


def frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """(open, high, low, close) rows as a bars frame (dates are irrelevant to the pure maths)."""
    return pd.DataFrame(
        [{"open": o, "high": h, "low": low, "close": c, "volume": 1e6} for o, h, low, c in rows]
    )


# ------------------------------------------------------------- the pure maths


def test_long_whole_bars_count_when_the_position_is_still_held():
    # entry 100, stop 95 (risk 5). Highest high 108, lowest low 98.
    bars = frame([(100, 104, 98, 102), (102, 108, 99, 107)])
    result = compute_excursion("long", 100.0, 95.0, bars, last_bar="complete")
    assert result.mfe_pct == pytest.approx(8.0)
    assert result.mae_pct == pytest.approx(2.0)
    assert result.mfe_r == pytest.approx(1.6)  # 8 / 5
    assert result.mae_r == pytest.approx(0.4)  # 2 / 5
    assert result.bars_used == 2


def test_short_mirrors_the_long():
    # entry 100, stop 105 (risk 5). Favourable = lows (94), adverse = highs (103).
    bars = frame([(100, 103, 97, 98), (98, 99, 94, 95)])
    result = compute_excursion("short", 100.0, 105.0, bars, last_bar="complete")
    assert result.mfe_pct == pytest.approx(6.0)
    assert result.mae_pct == pytest.approx(3.0)
    assert result.mfe_r == pytest.approx(1.2)
    assert result.mae_r == pytest.approx(0.6)


def test_long_stop_exit_bar_counts_the_fill_not_the_bars_low_or_high():
    # Last bar is the stop bar: low 90 is far below the 95 fill (it may have come
    # after the position was gone), high 120 may have come either side. Only the
    # open (97) and the fill (95) are certain.
    bars = frame([(100, 104, 98, 102), (97, 120, 90, 92)])
    result = compute_excursion("long", 100.0, 95.0, bars, exit_price=95.0, last_bar="exit")
    assert result.mfe_pct == pytest.approx(4.0)  # 104 from the first bar, not 120
    assert result.mae_pct == pytest.approx(5.0)  # the fill (95), not the 90 low
    assert result.mae_r == pytest.approx(1.0)  # a stop exit is 1R adverse


def test_long_target_exit_bar_counts_the_fill_not_the_bars_other_extreme():
    # TP1 bar: its low (80) may have come after the exit, so it is not counted.
    bars = frame([(100, 104, 98, 102), (105, 111, 96, 110)])
    result = compute_excursion("long", 100.0, 95.0, bars, exit_price=110.0, last_bar="exit")
    assert result.mfe_pct == pytest.approx(10.0)
    assert result.mfe_r == pytest.approx(2.0)
    assert result.mae_pct == pytest.approx(2.0)  # first bar's low 98; never the 80


def test_short_target_exit_bar_mirrors():
    bars = frame([(100, 103, 97, 98), (98, 106, 94, 95)])
    result = compute_excursion("short", 100.0, 105.0, bars, exit_price=95.0, last_bar="exit")
    assert result.mfe_pct == pytest.approx(5.0)  # the 95 fill, not the 94 low
    assert result.mae_pct == pytest.approx(3.0)  # 103; never the exit bar's 106 high


def test_gap_through_the_stop_makes_the_adverse_move_larger_than_one_r():
    # Opens at 83, below a 95 stop: filled at 83. MAE is 17 = 3.4R, not 1R.
    bars = frame([(83, 90, 80, 88)])
    result = compute_excursion("long", 100.0, 95.0, bars, exit_price=83.0, last_bar="exit")
    assert result.mae_pct == pytest.approx(17.0)
    assert result.mae_r == pytest.approx(3.4)
    assert result.mfe_pct == pytest.approx(0.0)


def test_excursions_never_go_negative():
    # Never traded above entry: MFE is 0, not negative.
    bars = frame([(99, 99.5, 97, 98)])
    result = compute_excursion("long", 100.0, 95.0, bars, last_bar="complete")
    assert result.mfe_pct == 0.0
    assert result.mfe_r == 0.0


def test_no_bars_available_is_none_never_a_guess():
    assert compute_excursion("long", 100.0, 95.0, None, exit_price=103.0) is None


def test_empty_frame_is_a_real_answer_from_entry_and_exit_alone():
    # Opened and closed within the entry bar's session: the exit price is all there is.
    result = compute_excursion("long", 100.0, 95.0, frame([]), exit_price=103.0, last_bar="complete")
    assert result.mfe_pct == pytest.approx(3.0)
    assert result.mae_pct == 0.0
    assert result.bars_used == 0


def test_zero_risk_gives_no_r_values():
    result = compute_excursion("long", 100.0, 100.0, frame([(100, 105, 99, 104)]), last_bar="complete")
    assert result.mfe_pct == pytest.approx(5.0)
    assert result.mfe_r is None and result.mae_r is None


def test_bars_with_missing_values_are_skipped_not_invented():
    bars = frame([(100, 104, 98, 102), (100, float("nan"), float("nan"), 100)])
    result = compute_excursion("long", 100.0, 95.0, bars, last_bar="complete")
    assert result.mfe_pct == pytest.approx(4.0)
    assert result.mae_pct == pytest.approx(2.0)


# ------------------------------------------------------- the engine stores it


class BarsProvider:
    name = "fake"

    def __init__(self, bars: list[dict], price: float = 100.0, fail: Exception | None = None):
        self._bars = bars
        self.price = price
        self.fail = fail

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        if self.fail is not None:
            raise self.fail
        return pd.DataFrame(self._bars)

    def get_quote(self, symbol: str) -> QuoteData:
        return QuoteData(symbol=symbol, price=self.price, change_pct_24h=0.0, volume=1_000.0, avg_volume_20d=1e6)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def make_bars(start: datetime, rows: list[tuple[float, float, float, float]]) -> list[dict]:
    """One bar per day from `start`; rows[0] is the entry bar."""
    return [
        {"date": start + timedelta(days=i), "open": o, "high": h, "low": low, "close": c, "volume": 1e6}
        for i, (o, h, low, c) in enumerate(rows)
    ]


def open_long(session: Session, provider: BarsProvider, opened: datetime, **engine_kwargs) -> tuple[PaperTradingEngine, PaperPosition]:
    """A long at 100, stop 95, TP1 110, opened through the engine and then back-dated."""
    engine = PaperTradingEngine(session, provider, STARTING_CASH, **engine_kwargs)
    plan = TradePlanRecord(
        symbol="AAPL", direction="long", entry=100.0, stop=95.0, tp1=110.0, tp2=120.0, rr1=2.0, rr2=4.0,
        suggested_shares=10, account_risk_dollars=50.0, confidence_score=70,
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    position = engine.open_position(plan)
    position.opened_at = opened
    session.add(position)
    session.commit()
    return engine, position


def test_stop_hit_stores_the_excursion_up_to_the_stop(session: Session):
    opened = datetime(2026, 1, 5, 20, 0)
    start = datetime(2026, 1, 5)
    rows = [
        (100, 101, 99, 100),  # entry bar: spent, never counted
        (100, 104, 98, 102),
        (101, 106, 99, 104),
        (100, 101, 90, 92),  # stop bar: fills at the 95 stop; its 90 low is after the exit
        (95, 130, 50, 100),  # after the exit: must not matter
    ]
    engine, position = open_long(session, BarsProvider(make_bars(start, rows)), opened)

    engine.mark_to_market(snapshot=False)

    assert position.close_reason == "stop_hit"
    assert position.mfe_pct == pytest.approx(6.0)  # 106
    assert position.mae_pct == pytest.approx(5.0)  # the 95 fill
    assert position.mfe_r == pytest.approx(1.2)
    assert position.mae_r == pytest.approx(1.0)


def test_entry_bar_range_is_not_counted(session: Session):
    opened = datetime(2026, 1, 5, 20, 0)
    rows = [(100, 140, 60, 100), (100, 102, 99, 101), (100, 111, 100, 110)]  # huge entry bar, TP1 on the last
    engine, position = open_long(session, BarsProvider(make_bars(datetime(2026, 1, 5), rows)), opened)

    engine.mark_to_market(snapshot=False)

    assert position.close_reason == "tp1_hit"
    assert position.mfe_pct == pytest.approx(10.0)  # the 110 fill; the entry bar's 140 is not counted
    assert position.mae_pct == pytest.approx(1.0)  # 99, not the entry bar's 60


def test_tp1_hit_stores_the_excursion(session: Session):
    opened = datetime(2026, 1, 5, 20, 0)
    rows = [(100, 101, 99, 100), (100, 104, 98, 102), (105, 111, 96, 110)]
    engine, position = open_long(session, BarsProvider(make_bars(datetime(2026, 1, 5), rows)), opened)

    engine.mark_to_market(snapshot=False)

    assert position.close_reason == "tp1_hit"
    assert position.mfe_r == pytest.approx(2.0)  # the 110 fill
    assert position.mae_pct == pytest.approx(2.0)  # 98 on the first bar; the 96 low is on the exit bar
    assert position.mae_r == pytest.approx(0.4)


def test_time_exit_counts_the_whole_final_bar(session: Session):
    # Friday 4 Sep 2026 entry; the time exit fires at the close of the 2nd bar after it.
    opened = datetime(2026, 9, 4, 15, 0)
    rows = [(100, 101, 99, 100), (100, 103, 98, 101), (101, 105, 97, 102)]
    bars = make_bars(datetime(2026, 9, 4), rows)
    # Skip the weekend/holiday: keep real trading dates for the three bars.
    for bar, day in zip(bars, ("2026-09-04", "2026-09-08", "2026-09-09")):
        bar["date"] = pd.Timestamp(day)
    now = datetime(2026, 9, 10, 16, 0)
    engine, position = open_long(session, BarsProvider(bars), opened, max_holding_days=2, clock=lambda: now)
    # open_long opened through the engine with this clock (a trading day at 12:00 ET): re-date the open.
    position.opened_at = opened
    session.add(position)
    session.commit()

    engine.mark_to_market(snapshot=False)

    assert position.close_reason == "time_exit"
    assert position.mfe_pct == pytest.approx(5.0)  # the finished last bar's 105 high counts
    assert position.mae_pct == pytest.approx(3.0)  # and its 97 low


def test_manual_close_fetches_the_bars_best_effort(session: Session):
    opened = datetime(2026, 1, 5, 20, 0)
    rows = [(100, 101, 99, 100), (100, 104, 98, 102), (102, 107, 99, 104)]
    engine, position = open_long(session, BarsProvider(make_bars(datetime(2026, 1, 5), rows)), opened)

    engine.close_position(position, 103.0, "manual")

    assert position.status == "closed"
    assert position.mfe_pct == pytest.approx(7.0)
    assert position.mae_pct == pytest.approx(2.0)
    assert position.mfe_r == pytest.approx(1.4)


def test_manual_close_within_the_entry_session_uses_entry_and_exit_only(session: Session):
    opened = datetime(2026, 1, 5, 20, 0)
    rows = [(100, 101, 99, 100)]  # only the entry bar exists
    engine, position = open_long(session, BarsProvider(make_bars(datetime(2026, 1, 5), rows)), opened)

    engine.close_position(position, 103.0, "manual")

    assert position.mfe_pct == pytest.approx(3.0)
    assert position.mae_pct == 0.0


@pytest.mark.parametrize("failure", [AllProvidersFailedError("down"), RuntimeError("boom")])
def test_close_still_succeeds_when_the_bars_cannot_be_fetched(session: Session, failure):
    opened = datetime(2026, 1, 5, 20, 0)
    provider = BarsProvider([], fail=None)
    engine, position = open_long(session, provider, opened)
    provider.fail = failure  # the bar fetch fails only now, at close time

    engine.close_position(position, 103.0, "manual")

    assert position.status == "closed"
    assert position.realized_pnl == pytest.approx(30.0)
    assert position.mfe_pct is None and position.mae_r is None  # nothing guessed


def test_a_stop_or_target_closed_without_the_scan_has_no_figures(session: Session):
    # Which bar a level was hit on is only known to the exit scan; a bare
    # close_position call can't say, so it records nothing rather than guess.
    opened = datetime(2026, 1, 5, 20, 0)
    rows = [(100, 101, 99, 100), (100, 104, 98, 102)]
    engine, position = open_long(session, BarsProvider(make_bars(datetime(2026, 1, 5), rows)), opened)

    engine.close_position(position, 95.0, "stop_hit")

    assert position.status == "closed"
    assert position.mfe_pct is None


def test_no_figures_when_the_entry_bar_is_outside_the_history_window(session: Session):
    # Every bar postdates the entry (a position older than the fetched history):
    # the early part of the trade is missing, so a stop exit has no honest range.
    opened = datetime(2026, 1, 5, 20, 0)
    rows = [(100, 101, 99, 100), (100, 104, 98, 102), (100, 101, 90, 92)]
    engine, position = open_long(session, BarsProvider(make_bars(datetime(2026, 1, 8), rows)), opened)

    engine.mark_to_market(snapshot=False)

    assert position.close_reason == "stop_hit"
    assert position.mfe_pct is None and position.mae_pct is None


# ----------------------------------------------------------------- the stats


def closed_row(pnl: float, r: float, mfe_r: float | None, mae_r: float | None, symbol: str = "AAPL") -> PaperPosition:
    return PaperPosition(
        symbol=symbol, direction="long", entry_price=100.0, stop_loss=95.0, tp1=110.0, tp2=120.0, shares=10,
        status="closed", close_price=100.0 + pnl / 10, close_reason="tp1_hit" if pnl > 0 else "stop_hit",
        realized_pnl=pnl, realized_r=r, mfe_r=mfe_r, mae_r=mae_r,
        mfe_pct=None if mfe_r is None else mfe_r * 5, mae_pct=None if mae_r is None else mae_r * 5,
    )


def test_stats_average_winners_and_losers_separately():
    rows = [
        closed_row(100, 2.0, 2.0, 0.4),  # winner
        closed_row(60, 1.2, 2.0, 0.8),  # winner that came within 0.2R of the stop
        closed_row(-50, -1.0, 1.4, 1.0),  # loser that was 1.4R ahead first
        closed_row(-50, -1.0, 0.2, 1.0),
        closed_row(-50, -1.0, 0.6, 1.2),
        closed_row(30, 0.6, None, None),  # closed before the figures existed: left out
    ]
    stats = compute_excursion_stats(rows)

    assert stats.closed_trades == 6
    assert stats.measured == 5
    assert stats.winners.n == 2
    assert stats.winners.avg_mfe_r == pytest.approx(2.0)
    assert stats.winners.avg_mae_r == pytest.approx(0.6)
    assert stats.losers.n == 3
    assert stats.losers.avg_mfe_r == pytest.approx((1.4 + 0.2 + 0.6) / 3)
    assert stats.losers.avg_mae_r == pytest.approx((1.0 + 1.0 + 1.2) / 3)
    assert stats.losers_reached_1r == 1
    assert stats.winners_near_stop == 1
    # exit efficiency: realised R / MFE R per winner, averaged: (2/2 + 1.2/2) / 2
    assert stats.exit_efficiency == pytest.approx(0.8)
    assert stats.exit_efficiency_n == 2


def test_stats_are_none_not_zero_without_rows():
    stats = compute_excursion_stats([closed_row(30, 0.6, None, None)])
    assert stats.measured == 0
    assert stats.winners.avg_mfe_r is None and stats.losers.avg_mae_r is None
    assert stats.exit_efficiency is None
    assert compute_excursion_stats([]).closed_trades == 0


def test_portfolio_stats_carry_the_excursion_block(session: Session):
    session.add(AccountState(starting_cash=STARTING_CASH, current_cash=STARTING_CASH))
    session.add(closed_row(100, 2.0, 2.0, 0.4))
    session.add(closed_row(-50, -1.0, 1.4, 1.0))
    session.commit()

    result = compute_portfolio_stats(session, BarsProvider([]), STARTING_CASH)

    assert result.excursions["measured"] == 2
    assert result.excursions["winners"]["avg_mfe_r"] == pytest.approx(2.0)
    assert result.excursions["losers"]["avg_mae_r"] == pytest.approx(1.0)


# --------------------------------------------- live figures for open positions


def test_live_excursion_of_an_open_position(session: Session):
    opened = datetime(2026, 1, 5, 20, 0)
    rows = [(100, 120, 60, 100), (100, 104, 98, 102), (102, 106, 97, 104)]  # entry bar is huge and ignored
    provider = BarsProvider(make_bars(datetime(2026, 1, 5), rows))
    _, position = open_long(session, provider, opened)

    live = live_excursion(position, provider)

    assert live.mfe_pct == pytest.approx(6.0)
    assert live.mae_pct == pytest.approx(3.0)
    assert live.mfe_r == pytest.approx(1.2)
    assert live.mae_r == pytest.approx(0.6)
    assert position.mfe_pct is None  # computed on the fly, never stored


def test_live_excursion_is_none_without_bars_or_without_the_entry_bar(session: Session):
    opened = datetime(2026, 1, 5, 20, 0)
    _, position = open_long(session, BarsProvider([]), opened)
    assert live_excursion(position, BarsProvider([], fail=AllProvidersFailedError("down"))) is None
    late = BarsProvider(make_bars(datetime(2026, 1, 8), [(100, 104, 98, 102)]))
    assert live_excursion(position, late) is None


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(AccountState(starting_cash=STARTING_CASH, current_cash=STARTING_CASH - 1000))
        s.add(
            PaperPosition(
                symbol="AAPL", direction="long", entry_price=100.0, stop_loss=95.0, tp1=130.0, tp2=140.0, shares=10,
                opened_at=datetime(2026, 1, 5, 20, 0),
            )
        )
        s.add(closed_row(100, 2.0, 2.0, 0.4, symbol="MSFT"))
        s.commit()
    return engine


def test_positions_endpoint_returns_live_excursions_without_writing(db):
    rows = [(100, 101, 99, 100), (100, 104, 98, 102), (102, 106, 97, 104)]
    provider = BarsProvider(make_bars(datetime(2026, 1, 5), rows))

    def _session():
        with Session(db) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_data_provider] = lambda: provider
    app.dependency_overrides[get_app_settings] = lambda: AppSettings()
    try:
        client = TestClient(app)
        body = client.get("/api/portfolio/positions").json()
        stats = client.get("/api/portfolio/stats").json()
    finally:
        for dependency in (get_session, get_data_provider, get_app_settings):
            app.dependency_overrides.pop(dependency, None)

    by_symbol = {p["symbol"]: p for p in body}
    assert by_symbol["AAPL"]["status"] == "open"
    assert by_symbol["AAPL"]["mfe_r"] == pytest.approx(1.2)
    assert by_symbol["AAPL"]["mae_r"] == pytest.approx(0.6)
    assert by_symbol["MSFT"]["mfe_r"] == pytest.approx(2.0)  # the stored figure
    assert stats["excursions"]["measured"] == 1
    assert stats["excursions"]["winners"]["avg_mfe_r"] == pytest.approx(2.0)
    with Session(db) as s:
        stored = s.exec(select(PaperPosition).where(PaperPosition.symbol == "AAPL")).one()
        assert stored.mfe_r is None  # the GET wrote nothing


# ------------------------------------------------------------------ backfill

NOW = datetime(2026, 1, 20)


def old_closed(session: Session, reason: str, close_price: float, closed_at: datetime, **kwargs) -> PaperPosition:
    position = PaperPosition(
        symbol="AAPL", direction="long", entry_price=100.0, stop_loss=95.0, tp1=110.0, tp2=120.0, shares=10,
        opened_at=datetime(2026, 1, 5, 20, 0), status="closed", closed_at=closed_at, close_price=close_price,
        close_reason=reason, realized_pnl=(close_price - 100) * 10, realized_r=(close_price - 100) / 5, **kwargs,
    )
    session.add(position)
    session.commit()
    session.refresh(position)
    return position


def test_backfill_locates_a_late_closed_stop_exit_and_ignores_later_bars(session: Session):
    rows = [
        (100, 101, 99, 100),  # Jan 5 entry bar
        (100, 104, 98, 102),  # Jan 6
        (100, 101, 90, 92),  # Jan 7: the stop bar (fill 95)
        (95, 200, 10, 100),  # Jan 8: after the exit, closed late (Jan 9): must not count
    ]
    provider = BarsProvider(make_bars(datetime(2026, 1, 5), rows))
    position = old_closed(session, "stop_hit", 95.0, datetime(2026, 1, 9, 15, 0))

    summary = backfill_excursions(session, provider, now=NOW)

    assert summary.updated == 1
    assert position.mfe_pct == pytest.approx(4.0)
    assert position.mae_pct == pytest.approx(5.0)
    assert position.mae_r == pytest.approx(1.0)


def test_backfill_tp1_exit(session: Session):
    rows = [(100, 101, 99, 100), (100, 104, 98, 102), (105, 111, 96, 110)]
    provider = BarsProvider(make_bars(datetime(2026, 1, 5), rows))
    position = old_closed(session, "tp1_hit", 110.0, datetime(2026, 1, 7, 15, 0))

    backfill_excursions(session, provider, now=NOW)

    assert position.mfe_r == pytest.approx(2.0)
    assert position.mae_pct == pytest.approx(2.0)


def test_backfill_is_idempotent_and_leaves_recorded_rows_alone(session: Session):
    rows = [(100, 101, 99, 100), (100, 104, 98, 102), (105, 111, 96, 110)]
    provider = BarsProvider(make_bars(datetime(2026, 1, 5), rows))
    old_closed(session, "tp1_hit", 110.0, datetime(2026, 1, 7, 15, 0))
    recorded = old_closed(session, "tp1_hit", 110.0, datetime(2026, 1, 7, 15, 0), mfe_pct=1.0, mae_pct=1.0, mfe_r=0.2, mae_r=0.2)

    first = backfill_excursions(session, provider, now=NOW)
    second = backfill_excursions(session, provider, now=NOW)

    assert (first.updated, first.already_recorded) == (1, 1)
    assert (second.updated, second.already_recorded) == (0, 2)
    assert recorded.mfe_r == 0.2  # untouched without overwrite=True
    assert backfill_excursions(session, provider, now=NOW, overwrite=True).updated == 2
    assert recorded.mfe_r == pytest.approx(2.0)


def test_backfill_skips_rows_with_no_bars_and_never_guesses(session: Session):
    position = old_closed(session, "tp1_hit", 110.0, datetime(2026, 1, 7, 15, 0))

    summary = backfill_excursions(session, BarsProvider([], fail=AllProvidersFailedError("down")), now=NOW)

    assert summary.updated == 0
    assert summary.skipped == {"no price history available": 1}
    assert position.mfe_pct is None


def test_backfill_skips_when_the_stored_exit_does_not_match_the_bars(session: Session):
    rows = [(100, 101, 99, 100), (100, 104, 98, 102), (100, 105, 99, 103)]  # no level is ever touched
    position = old_closed(session, "stop_hit", 95.0, datetime(2026, 1, 7, 15, 0))

    summary = backfill_excursions(session, BarsProvider(make_bars(datetime(2026, 1, 5), rows)), now=NOW)

    assert summary.updated == 0 and position.mfe_pct is None


def test_backfill_manual_close_after_the_session_counts_the_whole_last_bar(session: Session):
    rows = [(100, 101, 99, 100), (100, 104, 98, 102), (102, 108, 96, 105), (105, 130, 70, 100)]
    provider = BarsProvider(make_bars(datetime(2026, 1, 5), rows))
    # Closed Jan 7 at 23:00 UTC (6 pm ET): the Jan 7 bar had finished, Jan 8 had not begun.
    position = old_closed(session, "manual", 105.0, datetime(2026, 1, 7, 23, 0))

    backfill_excursions(session, provider, now=NOW)

    assert position.mfe_pct == pytest.approx(8.0)
    assert position.mae_pct == pytest.approx(4.0)


def test_backfill_manual_close_during_a_session_counts_only_the_open_and_the_fill(session: Session):
    rows = [(100, 101, 99, 100), (100, 104, 98, 102), (102, 108, 96, 105)]
    provider = BarsProvider(make_bars(datetime(2026, 1, 5), rows))
    # Closed Jan 7 at 15:00 UTC (10 am ET), mid-session: that bar's 108 high / 96 low may come later.
    position = old_closed(session, "manual", 103.0, datetime(2026, 1, 7, 15, 0))

    backfill_excursions(session, provider, now=NOW)

    assert position.mfe_pct == pytest.approx(4.0)  # Jan 6's 104; Jan 7 only its open (102) and the 103 fill
    assert position.mae_pct == pytest.approx(2.0)  # Jan 6's 98


def test_backfill_time_exit_needs_a_matching_final_bar(session: Session):
    rows = [(100, 101, 99, 100), (100, 103, 98, 101), (101, 105, 97, 102)]
    provider = BarsProvider(make_bars(datetime(2026, 1, 5), rows))
    matching = old_closed(session, "time_exit", 102.0, datetime(2026, 1, 7, 23, 0))
    mismatched = old_closed(session, "time_exit", 120.0, datetime(2026, 1, 7, 23, 0))

    summary = backfill_excursions(session, provider, now=NOW)

    assert summary.updated == 1
    assert matching.mfe_pct == pytest.approx(5.0)  # the finished last bar counts in full
    assert matching.mae_pct == pytest.approx(3.0)
    assert mismatched.mfe_pct is None


def test_backfill_skips_positions_older_than_the_longest_window(session: Session):
    position = old_closed(session, "manual", 105.0, datetime(2026, 1, 7, 23, 0))
    summary = backfill_excursions(session, BarsProvider([]), now=datetime(2032, 1, 1))
    assert summary.updated == 0 and position.mfe_pct is None
    assert "older than the longest history window" in summary.skipped


def test_existing_databases_gain_the_excursion_columns(monkeypatch, tmp_path):
    """A position table written before these columns existed picks them up
    (as NULL) through the additive migration, so old closed rows read back None."""
    import app.database as database_module

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE paperposition (id INTEGER PRIMARY KEY, symbol VARCHAR NOT NULL, direction VARCHAR NOT NULL, "
            "entry_price FLOAT NOT NULL, stop_loss FLOAT NOT NULL, tp1 FLOAT NOT NULL, tp2 FLOAT NOT NULL, "
            "shares INTEGER NOT NULL, opened_at DATETIME NOT NULL, status VARCHAR NOT NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO paperposition (symbol, direction, entry_price, stop_loss, tp1, tp2, shares, opened_at, status) "
            "VALUES ('AAPL', 'long', 100, 95, 110, 120, 10, '2026-01-01 00:00:00', 'closed')"
        )
        conn.commit()
    monkeypatch.setattr(database_module, "engine", engine)

    database_module._add_missing_columns()

    with Session(engine) as s:
        row = s.exec(select(PaperPosition)).one()
    assert row.mfe_pct is None and row.mae_pct is None and row.mfe_r is None and row.mae_r is None
