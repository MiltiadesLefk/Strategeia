"""The scorecard: every line's pass / fail / insufficient-data state, and the standing banners."""

from __future__ import annotations

from datetime import date

import pytest

from app.backtest.metrics import EquityRow, TradeRow, compute_metrics
from app.backtest.scorecard import FAIL, INSUFFICIENT, PASS, Criteria, build_scorecard

CASH = 100_000.0
THREE_YEARS = [
    EquityRow(date(2021, 1, 4), 101_000.0),
    EquityRow(date(2021, 12, 31), 110_000.0),  # +10%
    EquityRow(date(2022, 12, 30), 99_000.0),  # -10%: worst fall 110k -> 99k
    EquityRow(date(2023, 12, 29), 120_000.0),  # +21.2%
]
COSTS = {"effective_settings": {"slippage_bps": 5.0, "commission_per_trade": 1.0}}


def closed(pnl, r):
    return TradeRow("AAA", "long", "closed", date(2021, 1, 4), date(2021, 1, 8), pnl, r, 4, "tp1_hit", 1.0)


TRADES = [closed(100.0, 1.0), closed(-50.0, -0.5), closed(80.0, 0.8)]


def metrics_for(equity=THREE_YEARS, trades=TRADES):
    return compute_metrics(equity, trades, CASH)


def comparison(strategy_return, spy_return, strategy_sharpe, spy_sharpe):
    return {
        "available": True,
        "comparison": {
            "strategy_return_pct": strategy_return, "spy_return_pct": spy_return, "excess_return_pct": strategy_return - spy_return,
            "strategy_sharpe": strategy_sharpe, "spy_sharpe": spy_sharpe,
        },
        "spy": {},
    }


def baseline_with(percentile, seeds):
    return {
        "available": True, "seeds": [{}] * seeds, "caveat": "small K caveat",
        "placement": {"total_return_pct": {"percentile": percentile, "n": seeds}},
    }


def statuses(card):
    return {c["key"]: c["status"] for c in card["checks"]}


def test_every_line_is_judged_against_its_own_criterion():
    card = build_scorecard(
        metrics_for(), comparison(20.0, 12.0, 1.1, 0.9), baseline_with(90.0, 20), None, COSTS,
        Criteria(min_trades=3, max_drawdown_pct=20.0),
    )
    assert statuses(card) == {
        "trades": PASS,  # 3 closed trades, 3 wanted
        "after_costs": PASS,  # +20% with costs charged
        "years": PASS,  # 2 of 3 full years positive
        "drawdown": PASS,  # 10% < 20%
        "beats_spy_return": PASS,
        "beats_spy_risk_adjusted": PASS,
        "beats_baseline": PASS,  # 90th percentile of 20 random runs
    }
    assert card["counts"] == {PASS: 7, FAIL: 0, INSUFFICIENT: 0, "total": 7}
    lines = {c["key"]: c for c in card["checks"]}
    assert lines["trades"]["actual"] == "3 closed trades" and "3" in lines["trades"]["criterion"]
    assert lines["years"]["actual"] == "2 of 3 full years positive"
    assert lines["drawdown"]["actual"] == "10.0% at the worst"
    assert "20" in lines["drawdown"]["criterion"]


def test_failing_lines_say_so_and_criteria_are_parameters_not_hard_caps():
    card = build_scorecard(
        metrics_for(), comparison(5.0, 12.0, 0.4, 0.9), baseline_with(40.0, 20), None, COSTS,
        Criteria(min_trades=200, max_drawdown_pct=5.0, baseline_percentile=75.0),
    )
    assert statuses(card) == {
        "trades": FAIL, "after_costs": PASS, "years": PASS, "drawdown": FAIL,
        "beats_spy_return": FAIL, "beats_spy_risk_adjusted": FAIL, "beats_baseline": FAIL,
    }
    assert card["criteria"] == {"min_trades": 200, "max_drawdown_pct": 5.0, "year_share": 0.5, "baseline_percentile": 75.0}
    assert "not a verdict" in card["note"].lower()
    assert "verdict" not in " ".join(c["label"] for c in card["checks"]).lower()  # lines state facts; there is no total score
    assert set(card["counts"]) == {PASS, FAIL, INSUFFICIENT, "total"}


def test_a_loss_after_costs_fails():
    losing = compute_metrics([EquityRow(date(2021, 1, 4), 95_000.0), EquityRow(date(2021, 12, 31), 90_000.0)], TRADES, CASH)
    assert statuses(build_scorecard(losing, None, None, None, COSTS))["after_costs"] == FAIL


def test_a_run_that_charged_no_costs_cannot_be_judged_after_costs():
    free = {"effective_settings": {"slippage_bps": 0.0, "commission_per_trade": 0.0}}
    card = build_scorecard(metrics_for(), None, None, None, free)
    line = next(c for c in card["checks"] if c["key"] == "after_costs")
    assert line["status"] == INSUFFICIENT and "no costs" in line["actual"] and "zero slippage" in line["detail"]


def test_calendar_years_need_two_full_years_and_partial_ones_do_not_count():
    one_full_year = [EquityRow(date(2021, 1, 4), 101_000.0), EquityRow(date(2021, 12, 31), 110_000.0), EquityRow(date(2022, 3, 1), 112_000.0)]
    line = next(c for c in build_scorecard(metrics_for(one_full_year), None, None, None, COSTS)["checks"] if c["key"] == "years")
    assert line["status"] == INSUFFICIENT and line["actual"] == "1 full calendar year" and "partial" in line["detail"]
    mostly_down = [
        EquityRow(date(2021, 1, 4), 99_000.0), EquityRow(date(2021, 12, 31), 95_000.0),
        EquityRow(date(2022, 12, 30), 90_000.0), EquityRow(date(2023, 12, 29), 92_000.0),
    ]
    assert statuses(build_scorecard(metrics_for(mostly_down), None, None, None, COSTS))["years"] == FAIL  # 1 of 3 up


def test_without_spy_or_a_baseline_those_lines_are_insufficient_not_failed():
    card = build_scorecard(metrics_for(), {"available": False, "reason": "SPY history is not stored", "spy": {"reason": "SPY history is not stored"}}, {"available": False, "reason": "none was run"}, None, COSTS)
    s = statuses(card)
    assert s["beats_spy_return"] == s["beats_spy_risk_adjusted"] == s["beats_baseline"] == INSUFFICIENT
    spy = next(c for c in card["checks"] if c["key"] == "beats_spy_return")
    assert spy["detail"] == "SPY history is not stored"
    assert next(c for c in card["checks"] if c["key"] == "beats_baseline")["detail"] == "none was run"
    assert card["counts"][INSUFFICIENT] == 3


def test_a_baseline_with_too_few_random_runs_is_insufficient_but_still_shows_where_the_run_sits():
    card = build_scorecard(metrics_for(), None, baseline_with(100.0, 5), None, COSTS)
    line = next(c for c in card["checks"] if c["key"] == "beats_baseline")
    assert line["status"] == INSUFFICIENT and "100th percentile of 5" in line["actual"] and "Fewer than 10" in line["detail"]


def test_a_missing_sharpe_makes_the_risk_adjusted_line_insufficient():
    card = build_scorecard(metrics_for(), comparison(5.0, 3.0, None, 0.9), None, None, COSTS)
    s = statuses(card)
    assert s["beats_spy_return"] == PASS and s["beats_spy_risk_adjusted"] == INSUFFICIENT


def test_an_empty_run_is_insufficient_where_a_number_is_needed():
    empty = compute_metrics([], [], CASH)
    s = statuses(build_scorecard(empty, None, None, None, COSTS))
    assert s["drawdown"] == INSUFFICIENT and s["after_costs"] == INSUFFICIENT and s["trades"] == FAIL


# ------------------------------------------------------------------ banners


def banners(card):
    return {b["key"]: b for b in card["banners"]}


COVERAGE = {
    "achievable_points": 9, "live_points_max": 16, "bar_reachable": True,
    "inactive_parts": [{"label": "Fundamentals"}, {"label": "News sentiment"}, {"label": "AI trading overlay (penalty)"}],
}


def test_the_honesty_banners_are_always_present():
    card = build_scorecard(metrics_for(), None, None, COVERAGE, COSTS)
    b = banners(card)
    assert list(b) == ["survivorship", "price_only", "sample_size", "daily_bars"]
    assert "today's index members" in b["survivorship"]["text"] and b["survivorship"]["level"] == "warning"
    assert "9 of the live 16 points" in b["price_only"]["text"] and "fundamentals" in b["price_only"]["text"] and "ai trading overlay" in b["price_only"]["text"]


def test_the_sample_size_banner_says_under_or_over_and_shows_the_r_interval():
    under = banners(build_scorecard(metrics_for(), None, None, COVERAGE, COSTS))["sample_size"]
    assert "3 closed trades is under the 200" in under["text"] and under["level"] == "warning"
    assert "95% interval on the average R" in under["text"]
    many = [closed(10.0 if i % 2 else -5.0, 0.2 if i % 2 else -0.1) for i in range(250)]
    over = banners(build_scorecard(metrics_for(trades=many), None, None, COVERAGE, COSTS))["sample_size"]
    assert "250 closed trades is at or over the 200" in over["text"] and over["level"] == "info"


def test_the_price_only_banner_still_appears_when_a_run_stored_no_coverage_and_flags_an_unreachable_bar():
    assert "Price-only core" in banners(build_scorecard(metrics_for(), None, None, None, COSTS))["price_only"]["text"]
    unreachable = banners(build_scorecard(metrics_for(), None, None, {**COVERAGE, "bar_reachable": False}, COSTS))["price_only"]
    assert "no trade could have been taken" in unreachable["text"]


def test_a_run_with_no_equity_still_builds_a_scorecard():
    card = build_scorecard(compute_metrics([], [], CASH), None, None, None, {})
    assert card["counts"]["total"] == 7 and len(card["banners"]) == 4
