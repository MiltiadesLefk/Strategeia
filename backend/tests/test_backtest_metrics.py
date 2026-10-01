"""Backtest statistics against numbers worked out by hand on tiny series."""

from __future__ import annotations

import math
from datetime import date

import pytest

from app.backtest.metrics import (
    EquityRow,
    TradeRow,
    compute_metrics,
    daily_returns,
    drawdown_curve,
    drawdown_summary,
    equity_rows,
    exit_reason_mix,
    longest_losing_streak,
    monthly_returns,
    sharpe_ratio,
    sortino_ratio,
    trade_rows,
    yearly_returns,
)

START = 100.0

# Five closes after a start of 100: +10%, -10%, +10%, flat, +10.19%.
SHORT_DAYS = [date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6), date(2024, 3, 7), date(2024, 3, 8)]
SHORT_EQUITY = [EquityRow(d, v, 1 if i in (1, 2) else 0) for i, (d, v) in enumerate(zip(SHORT_DAYS, [110, 99, 108.9, 108.9, 120]))]


def trade(symbol="AAA", direction="long", status="closed", pnl=100.0, r=1.0, holding=3, reason="tp1_hit", exit_day=date(2024, 3, 8), fees=0.0):
    return TradeRow(
        symbol=symbol, direction=direction, status=status, entry_date=date(2024, 3, 1),
        exit_date=exit_day if status == "closed" else None, realized_pnl=pnl, realized_r=r, holding_days=holding,
        close_reason=reason, fees_paid=fees,
    )


# ------------------------------------------------------------------ returns and risk


def test_daily_returns_start_from_the_starting_cash_and_compound_to_the_total():
    returns = daily_returns(START, SHORT_EQUITY)
    assert returns == pytest.approx([0.10, -0.10, 0.10, 0.0, 120 / 108.9 - 1])
    assert math.prod(1 + r for r in returns) == pytest.approx(1.20)


def test_sharpe_and_sortino_match_the_hand_calculation():
    returns = daily_returns(START, SHORT_EQUITY)
    # mean 0.0403857, sample std 0.0897697 -> 0.44987 x sqrt(252)
    assert sharpe_ratio(returns) == pytest.approx(7.14164, rel=1e-5)
    # downside deviation = sqrt(mean of squared negatives) = sqrt(0.01 / 5) = 0.0447214
    assert sortino_ratio(returns) == pytest.approx(14.33549, rel=1e-5)


def test_headline_numbers_on_a_short_run():
    m = compute_metrics(SHORT_EQUITY, [], START)
    assert m["returns"]["total_return_pct"] == pytest.approx(20.0)
    assert m["returns"]["volatility_pct"] == pytest.approx(142.50494, rel=1e-5)
    assert m["returns"]["best_day_pct"] == pytest.approx(10.192837, rel=1e-5)
    assert m["returns"]["worst_day_pct"] == pytest.approx(-10.0)
    # five calendar days: far too short to annualise
    assert m["period"]["calendar_days"] == 5 and m["period"]["annualised"] is False
    assert m["returns"]["cagr_pct"] is None and m["returns"]["calmar"] is None
    assert m["trades"]["trades_per_year"] is None


def test_max_drawdown_its_dates_and_the_time_underwater():
    summary = drawdown_summary(START, SHORT_EQUITY)
    # 110 -> 99 is the worst fall: 10%; it fell from the first close, bottomed on the second and recovered on the fifth
    assert summary["max_drawdown_pct"] == pytest.approx(10.0)
    assert summary["peak_date"] == SHORT_DAYS[0] and summary["trough_date"] == SHORT_DAYS[1]
    assert summary["recovery_date"] == SHORT_DAYS[4]
    assert summary["max_drawdown_days"] == 4  # trading days from the peak to the recovery
    assert summary["longest_underwater_days"] == 3  # days 2, 3 and 4 closed below 110
    assert summary["underwater_now"] is False

    curve = drawdown_curve(START, SHORT_EQUITY)
    assert [round(p["drawdown_pct"], 4) for p in curve] == [0.0, -10.0, -1.0, -1.0, 0.0]


def test_a_drawdown_that_never_recovers_has_no_recovery_date():
    rows = [EquityRow(date(2024, 3, 4 + i), v) for i, v in enumerate([120, 90, 95])]
    summary = drawdown_summary(100.0, rows)
    assert summary["max_drawdown_pct"] == pytest.approx(25.0)
    assert summary["recovery_date"] is None and summary["underwater_now"] is True
    assert summary["max_drawdown_days"] == 2  # peak (day 1) to the last day


def test_a_fall_straight_from_the_starting_cash_counts():
    rows = [EquityRow(date(2024, 3, 4), 80.0), EquityRow(date(2024, 3, 5), 100.0)]
    summary = drawdown_summary(100.0, rows)
    assert summary["max_drawdown_pct"] == pytest.approx(20.0)
    assert summary["peak_date"] is None  # the peak was the starting cash, before the first day
    assert summary["recovery_date"] == date(2024, 3, 5)


# ------------------------------------------------------------------ a run long enough to annualise


LONG_EQUITY = [
    EquityRow(date(2021, 1, 4), 105.0, 1),
    EquityRow(date(2021, 12, 31), 121.0, 0),
    EquityRow(date(2022, 6, 30), 110.0, 1),
    EquityRow(date(2022, 12, 30), 133.1, 0),
]


def test_cagr_and_calmar_over_two_years():
    m = compute_metrics(LONG_EQUITY, [], START)
    # 726 calendar days = 1.98768 years; 1.331 ^ (1 / 1.98768) - 1
    assert m["period"]["calendar_days"] == 726 and m["period"]["annualised"] is True
    assert m["returns"]["cagr_pct"] == pytest.approx(15.47125, rel=1e-5)
    # max drawdown 121 -> 110 = 9.0909%; Calmar = 15.47125 / 9.0909
    assert m["drawdown"]["max_drawdown_pct"] == pytest.approx(100 * (1 - 110 / 121))
    assert m["returns"]["calmar"] == pytest.approx(1.70184, rel=1e-5)


def test_yearly_and_monthly_return_tables():
    assert [(y["year"], round(y["return_pct"], 6), y["partial"]) for y in yearly_returns(START, LONG_EQUITY)] == [
        (2021, 21.0, False),  # 100 -> 121
        (2022, 10.0, False),  # 121 -> 133.1
    ]
    months = monthly_returns(START, LONG_EQUITY)
    assert [(m["year"], m["month"]) for m in months] == [(2021, 1), (2021, 12), (2022, 6), (2022, 12)]
    # each month is measured from the last close before it
    assert [round(m["return_pct"], 4) for m in months] == [5.0, round((121 / 105 - 1) * 100, 4), round((110 / 121 - 1) * 100, 4), round((133.1 / 110 - 1) * 100, 4)]


def test_a_run_that_starts_or_ends_mid_year_marks_that_year_partial():
    rows = [EquityRow(date(2021, 3, 1), 101.0), EquityRow(date(2021, 12, 30), 110.0), EquityRow(date(2022, 2, 1), 111.0)]
    years = yearly_returns(START, rows)
    assert [(y["year"], y["partial"]) for y in years] == [(2021, True), (2022, True)]


def test_exposure_is_the_share_of_days_with_a_position():
    m = compute_metrics(SHORT_EQUITY, [], START)
    assert m["exposure"]["days_with_a_position"] == 2
    assert m["exposure"]["exposure_pct"] == pytest.approx(40.0)
    assert m["exposure"]["average_open_positions"] == pytest.approx(0.4)


# ------------------------------------------------------------------ trades


TRADES = [
    trade(direction="long", pnl=300.0, r=1.5, holding=3, reason="tp1_hit", exit_day=date(2024, 3, 4), fees=1.0),
    trade(direction="short", pnl=-100.0, r=-1.0, holding=2, reason="stop_hit", exit_day=date(2024, 3, 5), fees=1.0),
    trade(direction="long", pnl=-100.0, r=-1.0, holding=5, reason="stop_hit", exit_day=date(2024, 3, 6), fees=1.0),
    trade(direction="long", pnl=200.0, r=2.0, holding=4, reason="tp1_hit", exit_day=date(2024, 3, 7), fees=1.0),
    trade(direction="long", status="open", pnl=50.0, r=0.25, holding=1, reason="open_at_end"),
]


def test_trade_statistics_match_the_hand_calculation():
    t = compute_metrics(SHORT_EQUITY, TRADES, START)["trades"]
    assert t["closed_trades"] == 4 and t["open_at_end"] == 1  # the open one is left out of every statistic
    assert t["wins"] == 2 and t["losses"] == 2 and t["win_rate_pct"] == pytest.approx(50.0)
    # Wilson 95% interval for 2 of 4
    assert t["win_rate_low_pct"] == pytest.approx(15.004, abs=0.01) and t["win_rate_high_pct"] == pytest.approx(84.996, abs=0.01)
    assert t["average_r"] == pytest.approx(0.375)  # (1.5 - 1 - 1 + 2) / 4
    assert t["average_r_low"] < 0.375 < t["average_r_high"]  # a bootstrap interval around it
    assert t["expectancy_usd"] == pytest.approx(75.0) and t["total_pnl"] == pytest.approx(300.0)
    assert t["gross_profit"] == pytest.approx(500.0) and t["gross_loss"] == pytest.approx(200.0)
    assert t["profit_factor"] == pytest.approx(2.5)
    assert t["average_win"] == pytest.approx(250.0) and t["average_loss"] == pytest.approx(-100.0)
    assert t["payoff_ratio"] == pytest.approx(2.5)
    assert t["average_win_r"] == pytest.approx(1.75) and t["average_loss_r"] == pytest.approx(-1.0)
    assert t["average_holding_days"] == pytest.approx(3.5)  # (3 + 2 + 5 + 4) / 4
    assert t["longest_losing_streak"] == 2
    assert t["total_fees"] == pytest.approx(4.0)


def test_trades_per_year_uses_the_calendar_span():
    t = compute_metrics(LONG_EQUITY, TRADES, START)["trades"]
    assert t["trades_per_year"] == pytest.approx(4 / (726 / 365.25))


def test_the_losing_streak_follows_the_order_trades_closed_not_the_order_they_opened():
    # opened win, loss, loss, win but closed in the order loss, loss, win, win -> still 2 in a row
    ordered = [
        trade(pnl=100.0, exit_day=date(2024, 3, 6)),
        trade(pnl=-5.0, exit_day=date(2024, 3, 4)),
        trade(pnl=-5.0, exit_day=date(2024, 3, 5)),
        trade(pnl=100.0, exit_day=date(2024, 3, 7)),
    ]
    assert longest_losing_streak(ordered) == 2
    # a trade that exactly broke even did not win, so it extends a streak
    assert longest_losing_streak([trade(pnl=0.0, exit_day=date(2024, 3, 4)), trade(pnl=-1.0, exit_day=date(2024, 3, 5))]) == 2


def test_exit_reason_mix_and_long_short_split():
    m = compute_metrics(SHORT_EQUITY, TRADES, START)
    mix = {row["reason"]: row for row in m["exit_reasons"]}
    assert mix["tp1_hit"]["count"] == 2 and mix["tp1_hit"]["share_pct"] == pytest.approx(40.0)
    assert mix["stop_hit"]["count"] == 2 and mix["stop_hit"]["average_r"] == pytest.approx(-1.0)
    assert mix["open_at_end"]["count"] == 1 and mix["open_at_end"]["share_pct"] == pytest.approx(20.0)
    assert sum(row["share_pct"] for row in m["exit_reasons"]) == pytest.approx(100.0)

    split = {row["direction"]: row for row in m["by_direction"]}
    assert split["long"]["trades"] == 3 and split["short"]["trades"] == 1
    assert split["long"]["win_rate_pct"] == pytest.approx(2 / 3 * 100)
    assert split["long"]["average_r"] == pytest.approx((1.5 - 1.0 + 2.0) / 3)
    assert split["short"]["win_rate_pct"] == pytest.approx(0.0) and split["short"]["total_pnl"] == pytest.approx(-100.0)
    assert split["long"]["share_pct"] == pytest.approx(75.0)


def test_profit_factor_is_undefined_without_a_loser():
    t = compute_metrics(SHORT_EQUITY, [trade(pnl=10.0), trade(pnl=20.0)], START)["trades"]
    assert t["profit_factor"] is None and t["payoff_ratio"] is None and t["average_loss"] is None
    assert t["win_rate_pct"] == pytest.approx(100.0)


# ------------------------------------------------------------------ degenerate input


def test_flat_equity_has_no_ratios_and_no_drawdown():
    flat = [EquityRow(date(2024, 3, 4 + i), 100_000.0) for i in range(5)]
    m = compute_metrics(flat, [], 100_000.0)
    assert m["returns"]["total_return_pct"] == 0.0
    assert m["returns"]["sharpe"] is None and m["returns"]["sortino"] is None
    assert m["returns"]["volatility_pct"] == 0.0
    assert m["drawdown"]["max_drawdown_pct"] == 0.0 and m["drawdown"]["recovery_date"] is None
    assert m["exposure"]["exposure_pct"] == 0.0


def test_no_trades_gives_undefined_trade_statistics_not_zeros():
    t = compute_metrics(SHORT_EQUITY, [], START)["trades"]
    assert t["closed_trades"] == 0 and t["total_pnl"] == 0.0
    for key in ("win_rate_pct", "win_rate_low_pct", "average_r", "average_r_low", "profit_factor", "payoff_ratio",
                "average_win", "average_loss", "expectancy_usd", "average_holding_days"):
        assert t[key] is None, key
    assert t["longest_losing_streak"] == 0
    assert exit_reason_mix([]) == []


def test_one_trade_has_a_win_rate_interval_but_no_r_interval():
    t = compute_metrics(SHORT_EQUITY, [trade(pnl=50.0, r=0.5)], START)["trades"]
    assert t["closed_trades"] == 1 and t["win_rate_pct"] == 100.0
    assert t["win_rate_low_pct"] is not None and t["win_rate_low_pct"] < 100.0  # one trade proves little
    assert t["average_r"] == 0.5 and t["average_r_low"] is None and t["average_r_high"] is None


def test_an_empty_run_does_not_raise():
    m = compute_metrics([], [], START)
    assert m["period"]["trading_days"] == 0 and m["period"]["first_day"] is None
    assert m["returns"]["total_return_pct"] == 0.0 and m["returns"]["sharpe"] is None
    assert m["drawdown"]["max_drawdown_pct"] == 0.0
    assert m["yearly_returns"] == [] and m["monthly_returns"] == [] and m["drawdown_series"] == []


def test_a_single_day_run():
    m = compute_metrics([EquityRow(date(2024, 3, 4), 101.0)], [], START)
    assert m["returns"]["total_return_pct"] == pytest.approx(1.0)
    assert m["returns"]["sharpe"] is None  # one return has no spread


# ------------------------------------------------------------------ input adapters


def test_rows_can_come_from_dicts_or_objects_and_are_sorted():
    class Row:
        def __init__(self, day, equity, open_positions):
            self.day, self.equity, self.open_positions = day, equity, open_positions

    rows = equity_rows([{"day": date(2024, 3, 5), "equity": 2.0, "cash": 0, "open_positions": 1}, Row(date(2024, 3, 4), 1.0, 0)])
    assert [r.day for r in rows] == [date(2024, 3, 4), date(2024, 3, 5)] and rows[1].open_positions == 1
    parsed = trade_rows([{"symbol": "A", "direction": "long", "status": "closed", "entry_date": date(2024, 3, 1),
                          "exit_date": date(2024, 3, 2), "realized_pnl": 1.0, "realized_r": 0.1, "holding_days": 1,
                          "close_reason": "tp1_hit", "fees_paid": 0.0}])
    assert parsed[0].symbol == "A" and parsed[0].close_reason == "tp1_hit"
