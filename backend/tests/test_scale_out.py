"""Partial scale-out at TP1 (I-4): opt-in, off by default.

With it ON, reaching TP1 sells `scale_out_fraction` of the shares (a limit fill), the
rest ("runner") gets a breakeven (or trailing) stop and runs to TP2, that stop, or the
time limit. One position row stays one trade: realized_pnl / realized_r are for the whole
trade and R is per the size at ENTRY. With it OFF nothing about an exit changes.

Long positions here sit at 100 with stop 95, TP1 110, TP2 120, 10 shares (risk 5/share,
50 total); 50% scale-out sells 5 at TP1 and keeps 5. Fake bars, injected clock, no network.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlmodel import Session, select

from app.config import AppSettings
from app.portfolio.engine import PaperTradingEngine
from app.portfolio.liquidity_slippage import impact_bps
from app.portfolio.models import AccountState, PaperPosition
from app.portfolio.stats import compute_portfolio_stats
from app.strategy.snapshot import build_snapshot, fingerprint
from tests.test_intraday_exits import (  # noqa: F401
    ENTRY_DAY,
    NEXT_DAY,
    STARTING_CASH,
    HourlyProvider,
    clock_at_et,
    daily_bar,
    make_position,
    session,
    us_day_hours,
)
from tests.test_time_exit import trading_days_after

LATE = datetime(2026, 10, 30, 21, 0)  # every test bar is long finished


def bars_for(rows: list[tuple[float, float, float, float]]) -> list[dict]:
    """The entry bar (quiet) then one daily bar per row (open, high, low, close)."""
    days = trading_days_after(ENTRY_DAY, len(rows))
    out = [daily_bar(ENTRY_DAY)]
    for day, (o, h, low, c) in zip(days, rows, strict=True):
        out.append(daily_bar(day, o=o, h=h, low=low, c=c))
    return out


def engine_for(session: Session, rows, *, now=None, **kwargs) -> tuple[PaperTradingEngine, HourlyProvider]:
    provider = HourlyProvider(bars_for(rows), hourly_mode="unsupported")
    moment = now or LATE
    kwargs.setdefault("scale_out_fraction", 0.5)
    return PaperTradingEngine(session, provider, STARTING_CASH, clock=lambda: moment, **kwargs), provider


def run(session: Session, rows, *, direction="long", position=None, **kwargs) -> PaperPosition:
    engine, _ = engine_for(session, rows, **kwargs)
    position = position or make_position(session, direction=direction)
    engine.mark_to_market(snapshot=False)
    session.refresh(position)
    return position


def cash(session: Session) -> float:
    return session.exec(select(AccountState)).first().current_cash


# ------------------------------------------------------------ the happy path


def test_tp1_sells_half_and_the_runner_reaches_tp2(session: Session):
    position = make_position(session)
    # Day 1 reaches 112 (TP1 at 110, low 101 stays above breakeven), day 2 gaps nowhere and reaches 121.
    run(session, [(101, 112, 101, 111), (112, 121, 111, 120)], position=position)

    assert position.status == "closed"
    assert position.close_reason == "tp2_hit"
    assert position.close_price == pytest.approx(120.0)
    assert (position.original_shares, position.partial_shares, position.shares) == (10, 5, 5)
    assert position.partial_fill_price == pytest.approx(110.0)
    assert position.partial_gross_pnl == pytest.approx(50.0)
    # One trade: 5 x 10 at TP1 plus 5 x 20 at TP2; R is per the 10 shares risked at entry (5 x 10 = 50).
    assert position.realized_pnl == pytest.approx(150.0)
    assert position.realized_r == pytest.approx(3.0)


def test_cash_matches_the_realised_pnl(session: Session):
    position = make_position(session)
    # make_position never debits the entry (it is a hand-built row), so only the exits move cash.
    run(session, [(101, 112, 101, 111), (112, 121, 111, 120)], position=position)
    assert cash(session) == pytest.approx(STARTING_CASH + 5 * 110.0 + 5 * 120.0)


def test_short_mirrors(session: Session):
    position = make_position(session, direction="short")  # stop 105, TP1 90, TP2 80
    run(session, [(99, 99.5, 89, 90), (88, 89, 79, 80)], direction="short", position=position)
    assert position.close_reason == "tp2_hit"
    assert position.partial_fill_price == pytest.approx(90.0)
    assert position.close_price == pytest.approx(80.0)
    assert position.realized_pnl == pytest.approx(5 * 10 + 5 * 20)
    assert position.realized_r == pytest.approx(3.0)


# ----------------------------------------------- stop after the partial (breakeven)


def test_the_runners_stop_is_breakeven_and_is_hit_before_a_later_target(session: Session):
    position = make_position(session)
    # Day 2 trades 99..109: through breakeven (100), never to TP2. Opens above it: fills at the level.
    run(session, [(101, 112, 101, 111), (108, 109, 99, 100)], position=position)
    assert position.close_reason == "stop_hit"
    assert position.close_price == pytest.approx(100.0)
    assert position.realized_pnl == pytest.approx(50.0)  # the partial's profit survives; the runner breaks even
    assert position.realized_r == pytest.approx(1.0)


def test_a_gap_below_breakeven_fills_at_the_open_not_the_level(session: Session):
    position = make_position(session)
    run(session, [(101, 112, 101, 111), (97, 98, 96, 97)], position=position)
    assert position.close_reason == "stop_hit"
    assert position.close_price == pytest.approx(97.0)  # the gap makes it worse, as for any stop
    assert position.realized_pnl == pytest.approx(50.0 + 5 * -3.0)


def test_the_original_stop_still_governs_before_tp1(session: Session):
    position = make_position(session)
    run(session, [(100, 111, 94, 100)], position=position)  # both original levels in one bar: stop first
    assert position.close_reason == "stop_hit"
    assert position.partial_fill_price is None  # never sold anything
    assert position.original_shares is None
    assert position.realized_pnl == pytest.approx(-50.0)
    assert position.exit_resolution == "daily_ambiguous_stop_first"


def test_the_tp1_bars_own_low_through_breakeven_stops_the_runner_that_day(session: Session):
    position = make_position(session)
    # TP1 bar low 99.5 is under breakeven: the unfavourable reading is that it came after TP1.
    run(session, [(101, 112, 99.5, 111)], position=position)
    assert position.close_reason == "stop_hit"
    assert position.close_price == pytest.approx(100.0)  # the stop did not exist before TP1: its own level
    assert position.realized_pnl == pytest.approx(50.0)


# ------------------------------------------------------ TP2: gaps and ambiguity


def test_a_gap_past_tp2_fills_at_the_open(session: Session):
    position = make_position(session)
    run(session, [(101, 112, 101, 111), (125, 126, 124, 125)], position=position)
    assert position.close_reason == "tp2_hit"
    assert position.close_price == pytest.approx(125.0)  # a resting limit fills at the better open
    assert position.realized_pnl == pytest.approx(50.0 + 5 * 25.0)


def test_a_gap_past_tp1_sells_the_partial_at_the_open(session: Session):
    position = make_position(session)
    run(session, [(115, 116, 114, 115)], position=position)
    assert position.partial_fill_price == pytest.approx(115.0)
    assert position.status == "open"
    assert position.shares == 5


def test_a_bar_holding_both_the_runners_stop_and_tp2_takes_the_stop(session: Session):
    position = make_position(session)
    run(session, [(101, 112, 101, 111), (110, 125, 99, 110)], position=position)
    assert position.close_reason == "stop_hit"
    assert position.exit_resolution == "daily_ambiguous_stop_first"
    assert position.realized_pnl == pytest.approx(50.0)


def test_tp1_and_tp2_on_the_same_bar_close_everything_that_day(session: Session):
    position = make_position(session)
    run(session, [(101, 121, 101, 119)], position=position)
    assert position.close_reason == "tp2_hit"
    assert position.partial_fill_price == pytest.approx(110.0)
    assert position.close_price == pytest.approx(120.0)
    assert position.realized_pnl == pytest.approx(150.0)


def test_tp1_bar_holding_the_runners_stop_and_tp2_takes_the_stop(session: Session):
    position = make_position(session)
    run(session, [(101, 121, 99.5, 110)], position=position)
    assert position.close_reason == "stop_hit"
    assert position.realized_pnl == pytest.approx(50.0)


# ----------------------------------------------------------------- time limit


def test_the_time_limit_closes_the_runner_at_the_close_and_keeps_the_partial(session: Session):
    position = make_position(session)
    rows = [(101, 112, 101, 111), (108, 109, 104, 105), (105, 106, 103, 104)]
    run(session, rows, position=position, max_holding_days=3)
    assert position.close_reason == "time_exit"
    assert position.close_price == pytest.approx(104.0)
    assert position.realized_pnl == pytest.approx(50.0 + 5 * 4.0)


def test_no_time_exit_before_the_limit(session: Session):
    position = make_position(session)
    run(session, [(101, 112, 101, 111), (108, 109, 104, 105)], position=position, max_holding_days=3)
    assert position.status == "open"
    assert position.partial_fill_price is not None


def test_the_time_exit_can_land_on_the_tp1_bar_itself(session: Session):
    position = make_position(session)
    run(session, [(101, 112, 101, 111)], position=position, max_holding_days=1)
    assert position.close_reason == "time_exit"
    assert position.close_price == pytest.approx(111.0)
    assert position.realized_pnl == pytest.approx(50.0 + 5 * 11.0)


# ---------------------------------------------------- resuming across sweeps


def test_a_later_sweep_resumes_at_the_partial_and_never_replays_earlier_bars(session: Session):
    position = make_position(session)
    # Day 1 dips to 99 (above the 95 stop, but UNDER breakeven): fine before the partial.
    day1 = (100, 101, 99, 100)
    tp1_day = (101, 112, 101, 111)
    engine, _ = engine_for(session, [day1, tp1_day])
    assert engine.mark_to_market(snapshot=False) == []
    session.refresh(position)
    assert position.partial_fill_price == pytest.approx(110.0)
    assert position.shares == 5 and position.status == "open"

    # A second sweep with more bars: day 1's 99 must not stop the runner, and nothing re-sells.
    engine2, _ = engine_for(session, [day1, tp1_day, (112, 113, 108, 110)])
    assert engine2.mark_to_market(snapshot=False) == []
    session.refresh(position)
    assert (position.shares, position.partial_shares, position.original_shares) == (5, 5, 10)

    engine3, _ = engine_for(session, [day1, tp1_day, (112, 113, 108, 110), (112, 121, 111, 120)])
    [closed] = engine3.mark_to_market(snapshot=False)
    assert closed.close_reason == "tp2_hit"
    assert closed.realized_pnl == pytest.approx(150.0)


def test_a_dip_on_the_partial_day_seen_only_by_a_later_sweep_still_stops_the_runner(session: Session):
    position = make_position(session)
    engine, _ = engine_for(session, [(101, 112, 101, 111)])
    engine.mark_to_market(snapshot=False)
    session.refresh(position)
    assert position.status == "open"
    # The same day's bar later shows a low of 99: it counts (the day is read again, not skipped).
    engine2, _ = engine_for(session, [(101, 112, 99, 111)])
    [closed] = engine2.mark_to_market(snapshot=False)
    assert closed.close_reason == "stop_hit"
    assert closed.realized_pnl == pytest.approx(50.0)


# ------------------------------------------------------------------- trailing


def test_trail_mode_follows_the_best_price_by_the_original_risk(session: Session):
    position = make_position(session)
    # TP1 day high 112 -> next day's stop is 112 - 5 = 107. Day 2 trades down to 106.
    run(
        session, [(101, 112, 101, 111), (110, 113, 106, 108)], position=position,
        scale_out_stop_mode="trail", scale_out_trail_r=1.0,
    )
    assert position.runner_trail_distance == pytest.approx(5.0)
    assert position.close_reason == "stop_hit"
    assert position.close_price == pytest.approx(107.0)
    assert position.realized_pnl == pytest.approx(50.0 + 5 * 7.0)


def test_breakeven_mode_would_have_stayed_open_on_the_same_bars(session: Session):
    position = make_position(session)
    run(session, [(101, 112, 101, 111), (110, 113, 106, 108)], position=position)
    assert position.status == "open"
    assert position.runner_trail_distance is None


def test_the_trail_never_goes_below_breakeven(session: Session):
    position = make_position(session)
    run(
        session, [(101, 110.5, 101, 105), (105, 106, 100.2, 104)], position=position,
        scale_out_stop_mode="trail", scale_out_trail_r=3.0,  # 110.5 - 15 = 95.5 < breakeven 100
    )
    assert position.status == "open"  # low 100.2 stays above the breakeven floor


# ------------------------------------------------------------- hourly resolution


def test_the_entry_days_hours_sell_at_tp1_and_stop_the_runner_the_same_day(session: Session):
    # Entry 11:00 ET. Hour 3 (12:30) reaches 111 (TP1); hour 5 (14:30) trades down to 99.
    hours = us_day_hours(ENTRY_DAY, {3: (100, 111, 100.5, 110), 4: (105, 106, 102, 105), 5: (108, 108, 99, 100)})
    provider = HourlyProvider([daily_bar(ENTRY_DAY)], hours)
    position = make_position(session, entry_day_check=None)
    engine = PaperTradingEngine(
        session, provider, STARTING_CASH, clock=clock_at_et(ENTRY_DAY, 17), scale_out_fraction=0.5
    )
    [closed] = engine.mark_to_market(snapshot=False)
    assert closed.close_reason == "stop_hit"
    assert closed.close_price == pytest.approx(100.0)
    assert closed.partial_fill_price == pytest.approx(100.0 * 1.1)
    assert closed.partial_at is not None
    assert closed.realized_pnl == pytest.approx(50.0)
    assert closed.entry_day_check == "hourly"


def test_the_entry_days_partial_is_resumed_by_a_later_sweep(session: Session):
    hours = us_day_hours(ENTRY_DAY, {3: (100, 111, 100.5, 110), 4: (105, 106, 102, 105)})
    provider = HourlyProvider([daily_bar(ENTRY_DAY)], hours)
    position = make_position(session, entry_day_check=None)
    PaperTradingEngine(
        session, provider, STARTING_CASH, clock=clock_at_et(ENTRY_DAY, 14), scale_out_fraction=0.5
    ).mark_to_market(snapshot=False)
    session.refresh(position)
    assert position.status == "open" and position.shares == 5

    hours2 = us_day_hours(ENTRY_DAY, {3: (100, 111, 100.5, 110), 4: (105, 106, 102, 105), 5: (108, 108, 99, 100)})
    provider2 = HourlyProvider([daily_bar(ENTRY_DAY)], hours2)
    # Scale-out switched OFF now: the runner is still managed to its end.
    [closed] = PaperTradingEngine(session, provider2, STARTING_CASH, clock=clock_at_et(ENTRY_DAY, 17)).mark_to_market(
        snapshot=False
    )
    assert closed.close_reason == "stop_hit"
    assert closed.realized_pnl == pytest.approx(50.0)


def test_a_day_holding_stop_and_tp1_reads_the_hours_and_sells_when_tp1_came_first(session: Session):
    # 8 Sep: reaches 111 and 94 (the original stop 95). Hours: TP1 first (hour 2), later 94 (hour 4).
    daily = [daily_bar(ENTRY_DAY), daily_bar(NEXT_DAY, o=100.0, h=111.0, low=94.0, c=100.0)]
    hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {2: (100, 110.5, 99, 110), 4: (110, 110, 94, 96)})
    provider = HourlyProvider(daily, hours)
    position = make_position(session)
    engine = PaperTradingEngine(session, provider, STARTING_CASH, clock=clock_at_et(NEXT_DAY, 17), scale_out_fraction=0.5)
    [closed] = engine.mark_to_market(snapshot=False)
    # Without scale-out this day closes at TP1; with it the half is sold and the runner is stopped at breakeven.
    assert closed.partial_fill_price == pytest.approx(110.0)
    assert closed.close_reason == "stop_hit"
    assert closed.close_price == pytest.approx(100.0)
    assert closed.exit_resolution in ("hourly", "hourly_ambiguous_stop_first")
    assert closed.realized_pnl == pytest.approx(50.0)


def test_a_day_holding_stop_and_tp1_with_the_stop_first_in_the_hours_is_a_plain_stop(session: Session):
    daily = [daily_bar(ENTRY_DAY), daily_bar(NEXT_DAY, o=100.0, h=111.0, low=94.0, c=100.0)]
    hours = us_day_hours(ENTRY_DAY) + us_day_hours(NEXT_DAY, {2: (100, 101, 94, 96), 4: (96, 110.5, 96, 110)})
    provider = HourlyProvider(daily, hours)
    position = make_position(session)
    engine = PaperTradingEngine(session, provider, STARTING_CASH, clock=clock_at_et(NEXT_DAY, 17), scale_out_fraction=0.5)
    [closed] = engine.mark_to_market(snapshot=False)
    assert closed.close_reason == "stop_hit"
    assert closed.partial_fill_price is None
    assert closed.realized_pnl == pytest.approx(-50.0)


# ------------------------------------------------------- small / odd positions


def test_a_one_share_position_cannot_split_and_exits_fully_at_tp1(session: Session):
    position = make_position(session)
    position.shares = 1
    session.add(position)
    session.commit()
    run(session, [(101, 112, 101, 111)], position=position)
    assert position.close_reason == "tp1_hit"
    assert position.partial_fill_price is None
    assert position.realized_pnl == pytest.approx(10.0)


# ------------------------------------------------------------------ flag off


SCENARIOS = {
    "tp1": [(101, 112, 101, 111), (112, 121, 111, 120)],
    "stop": [(100, 101, 94, 96)],
    "both": [(100, 111, 94, 100)],
    "time": [(101, 105, 100, 104), (104, 106, 101, 103), (103, 104, 100, 101)],
    "open": [(100, 104, 99, 102)],
}


def outcome(position: PaperPosition) -> tuple:
    return (
        position.status, position.close_reason, position.close_price, position.shares, position.realized_pnl,
        position.realized_r, position.fees_paid, position.exit_resolution, position.mfe_r, position.mae_r,
        position.original_shares, position.partial_fill_price, position.partial_gross_pnl, position.runner_stop,
    )


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("off", [None, 0.0], ids=["none", "zero"])
def test_with_scale_out_off_every_exit_is_the_full_exit(session: Session, name, off):
    position = make_position(session)
    run(session, SCENARIOS[name], position=position, scale_out_fraction=off, max_holding_days=3)
    assert position.partial_fill_price is None and position.original_shares is None
    if name == "tp1":
        assert (position.close_reason, position.close_price, position.realized_pnl) == ("tp1_hit", 110.0, 100.0)
        assert position.realized_r == pytest.approx(2.0)
        assert position.shares == 10
    if name == "time":
        assert (position.close_reason, position.close_price) == ("time_exit", 101.0)
    if name == "open":
        assert position.status == "open"


def test_off_is_byte_identical_to_an_engine_that_never_heard_of_scale_out(session: Session):
    """Same bars through an engine with the argument omitted and one given 0: every stored field matches."""
    results = []
    for kwargs in ({}, {"scale_out_fraction": None}, {"scale_out_fraction": 0.0, "scale_out_stop_mode": "trail"}):
        out = []
        for name in sorted(SCENARIOS):
            position = make_position(session, symbol=f"S{len(results)}{name.upper()}")
            provider = HourlyProvider(bars_for(SCENARIOS[name]), hourly_mode="unsupported")
            PaperTradingEngine(
                session, provider, STARTING_CASH, clock=lambda: LATE, max_holding_days=3, **kwargs
            ).mark_to_market(snapshot=False)
            session.refresh(position)
            out.append(outcome(position))
            position.status = "closed"  # leave nothing open for the next sweep to re-mark
            session.add(position)
            session.commit()
        results.append(out)
    assert results[0] == results[1] == results[2]


def test_the_strategy_fingerprint_is_unchanged_while_off_and_moves_when_on():
    base = fingerprint(build_snapshot(AppSettings()))
    assert fingerprint(build_snapshot(AppSettings(scale_out_fraction=0.3, scale_out_stop_mode="trail"))) == base
    on = fingerprint(build_snapshot(AppSettings(scale_out_enabled=True)))
    assert on != base
    assert fingerprint(build_snapshot(AppSettings(scale_out_enabled=True, scale_out_fraction=0.3))) != on
    assert fingerprint(build_snapshot(AppSettings(scale_out_enabled=True, scale_out_stop_mode="trail"))) != on
    # The trail multiple only matters in trail mode.
    assert fingerprint(build_snapshot(AppSettings(scale_out_enabled=True, scale_out_trail_r=2.0))) == on
    trail = fingerprint(build_snapshot(AppSettings(scale_out_enabled=True, scale_out_stop_mode="trail")))
    assert fingerprint(build_snapshot(AppSettings(scale_out_enabled=True, scale_out_stop_mode="trail", scale_out_trail_r=2.0))) != trail


# --------------------------------------------------------- stats and liquidity


def test_stats_count_one_trade_per_position_with_the_total_pnl_and_r(session: Session):
    position = make_position(session)
    run(session, [(101, 112, 101, 111), (112, 121, 111, 120)], position=position)
    stats = compute_portfolio_stats(session, HourlyProvider([], hourly_mode="unsupported"), STARTING_CASH)
    assert stats.total_trades == 1
    assert stats.win_rate == 100.0
    assert stats.avg_rr == pytest.approx(3.0)
    assert stats.exit_reasons == {"tp2_hit": 1}


def test_a_half_won_half_stopped_trade_is_one_win_not_two_rows(session: Session):
    position = make_position(session)
    run(session, [(101, 112, 101, 111), (108, 109, 99, 100)], position=position)
    stats = compute_portfolio_stats(session, HourlyProvider([], hourly_mode="unsupported"), STARTING_CASH)
    assert stats.total_trades == 1
    assert stats.win_rate == 100.0  # +50 total


def test_liquidity_slippage_prices_the_runners_exit_by_the_shares_it_still_holds(session: Session):
    position = make_position(session)
    provider = HourlyProvider(bars_for([(101, 112, 101, 111), (97, 98, 96, 97)]), hourly_mode="unsupported")
    provider.get_quote = lambda symbol: type(
        "Q", (), {"price": 100.0, "avg_volume_20d": 1_000.0, "change_pct_24h": 0.0, "volume": 1.0, "symbol": symbol}
    )()
    engine = PaperTradingEngine(
        session, provider, STARTING_CASH, clock=lambda: LATE, slippage_bps=5.0,
        liquidity_slippage_coefficient=100.0, scale_out_fraction=0.5,
    )
    [closed] = engine.mark_to_market(snapshot=False)
    kept_bps = 5.0 + impact_bps(5, 1_000.0, 100.0)
    assert closed.exit_slippage_bps == pytest.approx(kept_bps)
    assert closed.close_price == pytest.approx(97.0 * (1 - kept_bps / 10_000.0))
    # The partial was a limit fill: no slippage on it.
    assert closed.partial_fill_price == pytest.approx(110.0)


def test_commission_is_charged_on_the_partial_and_the_exit(session: Session):
    position = make_position(session)
    run(session, [(101, 112, 101, 111), (112, 121, 111, 120)], position=position, commission_per_trade=2.0)
    assert position.fees_paid == pytest.approx(4.0)  # the entry fee was never charged (hand-built row)
    assert position.realized_pnl == pytest.approx(150.0 - 4.0)
