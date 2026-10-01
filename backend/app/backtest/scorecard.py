"""A checklist for reading a backtest, not a verdict on it.

Each line states a criterion, the run's actual value, and whether it passed,
failed or could not be judged. Nothing here decides anything: no hard cap is
applied anywhere in the app because of a line failing, there is no total score,
and the criteria are parameters (the defaults are a reasonable starting bar, not a
law). "Insufficient data" is a real answer: a check that cannot be judged says so
instead of guessing.

Beside the checklist sit the standing honesty banners, which are shown for every
run because they are true of every run: the symbols are today's index members, only
the price-based part of the score was tested, and the trade count is (or is not)
enough to read a pattern from.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from app.backtest.baseline import MIN_SEEDS_FOR_READING

PASS = "pass"
FAIL = "fail"
INSUFFICIENT = "insufficient_data"

# Defaults for the criteria. Edit the numbers, or pass others per request.
DEFAULT_MIN_TRADES = 200
DEFAULT_MAX_DRAWDOWN_PCT = 20.0
DEFAULT_YEAR_SHARE = 0.5  # "most years": more than this share of full calendar years positive
DEFAULT_BASELINE_PERCENTILE = 75.0
# "Positive in most calendar years" needs at least this many complete years to mean anything.
MIN_FULL_YEARS = 2


@dataclass(frozen=True)
class Criteria:
    min_trades: int = DEFAULT_MIN_TRADES
    max_drawdown_pct: float = DEFAULT_MAX_DRAWDOWN_PCT
    year_share: float = DEFAULT_YEAR_SHARE
    baseline_percentile: float = DEFAULT_BASELINE_PERCENTILE


def _check(key: str, label: str, criterion: str, status: str, actual: str, detail: str | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "criterion": criterion, "status": status, "actual": actual, "detail": detail}


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:+.{digits}f}%"


def _num(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _trades_check(metrics: Mapping[str, Any], criteria: Criteria) -> dict[str, Any]:
    n = metrics["trades"]["closed_trades"]
    return _check(
        "trades", "Enough trades to read a pattern", f"at least {criteria.min_trades} closed trades",
        PASS if n >= criteria.min_trades else FAIL, f"{n} closed trades",
        "Win rate and average R from fewer trades than this move a lot with a few results.",
    )


def _after_costs_check(metrics: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    total = metrics["returns"]["total_return_pct"]
    effective = params.get("effective_settings") or {}
    slippage, commission = effective.get("slippage_bps"), effective.get("commission_per_trade")
    costs = f"slippage {_num(slippage, 1)} bps, commission ${_num(commission)} per trade"
    if total is None or not metrics["period"]["trading_days"]:
        return _check("after_costs", "Profitable after costs", "total return above 0", INSUFFICIENT, "no equity data")
    if slippage == 0 and commission == 0:
        return _check(
            "after_costs", "Profitable after costs", "total return above 0 with costs charged", INSUFFICIENT,
            f"{_pct(total)}, but no costs were charged", "This run used zero slippage and zero commission, so it says nothing about costs.",
        )
    return _check(
        "after_costs", "Profitable after costs", "total return above 0", PASS if total > 0 else FAIL, _pct(total), costs
    )


def _years_check(metrics: Mapping[str, Any], criteria: Criteria) -> dict[str, Any]:
    years = [y for y in metrics["yearly_returns"] if not y["partial"]]
    partial = len(metrics["yearly_returns"]) - len(years)
    criterion = f"more than {criteria.year_share * 100:.0f}% of full calendar years positive"
    if len(years) < MIN_FULL_YEARS:
        return _check(
            "years", "Positive in most calendar years", criterion, INSUFFICIENT,
            f"{len(years)} full calendar year{'s' if len(years) != 1 else ''}",
            f"At least {MIN_FULL_YEARS} complete years are needed" + (f" ({partial} partial year(s) are not counted)." if partial else "."),
        )
    positive = sum(1 for y in years if y["return_pct"] > 0)
    share = positive / len(years)
    return _check(
        "years", "Positive in most calendar years", criterion, PASS if share > criteria.year_share else FAIL,
        f"{positive} of {len(years)} full years positive", f"{partial} partial year(s) not counted" if partial else None,
    )


def _drawdown_check(metrics: Mapping[str, Any], criteria: Criteria) -> dict[str, Any]:
    if not metrics["period"]["trading_days"]:
        return _check("drawdown", "Drawdown under the limit", f"max drawdown under {criteria.max_drawdown_pct:g}%", INSUFFICIENT, "no equity data")
    worst = metrics["drawdown"]["max_drawdown_pct"]
    return _check(
        "drawdown", "Drawdown under the limit", f"max drawdown under {criteria.max_drawdown_pct:g}%",
        PASS if worst < criteria.max_drawdown_pct else FAIL, f"{worst:.1f}% at the worst",
        "Measured on daily closes: intraday dips are not seen. The limit is a yardstick only; no cap is enforced.",
    )


def _spy_checks(metrics: Mapping[str, Any], benchmarks: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    comparison = (benchmarks or {}).get("comparison")
    reason = ((benchmarks or {}).get("spy") or {}).get("reason") or (benchmarks or {}).get("reason") or "SPY history is not available"
    if not comparison:
        return [
            _check("beats_spy_return", "Beats SPY buy-and-hold on return", "strategy return above SPY's", INSUFFICIENT, "n/a", reason),
            _check("beats_spy_risk_adjusted", "Beats SPY on risk-adjusted return", "strategy Sharpe above SPY's", INSUFFICIENT, "n/a", reason),
        ]
    excess = comparison["excess_return_pct"]
    sharpe, spy_sharpe = comparison["strategy_sharpe"], comparison["spy_sharpe"]
    first = _check(
        "beats_spy_return", "Beats SPY buy-and-hold on return", "strategy return above SPY's",
        INSUFFICIENT if excess is None else (PASS if excess > 0 else FAIL),
        f"{_pct(comparison['strategy_return_pct'])} vs {_pct(comparison['spy_return_pct'])} (SPY)",
        "SPY is bought on the first day and held, with no costs.",
    )
    second = _check(
        "beats_spy_risk_adjusted", "Beats SPY on risk-adjusted return", "strategy Sharpe above SPY's",
        INSUFFICIENT if sharpe is None or spy_sharpe is None else (PASS if sharpe > spy_sharpe else FAIL),
        f"Sharpe {_num(sharpe)} vs {_num(spy_sharpe)} (SPY)", "Sharpe with a risk-free rate of 0, from daily returns.",
    )
    return [first, second]


def _baseline_check(baseline: Mapping[str, Any] | None, criteria: Criteria) -> dict[str, Any]:
    label, criterion = "Beats the random-entry baseline", f"at or above the {criteria.baseline_percentile:g}th percentile of random runs"
    if not baseline or not baseline.get("available"):
        reason = (baseline or {}).get("note") or (baseline or {}).get("reason") or "no random-entry baseline was run"
        return _check("beats_baseline", label, criterion, INSUFFICIENT, "n/a", reason)
    placement = (baseline.get("placement") or {}).get("total_return_pct")
    seeds = len(baseline.get("seeds", []))
    if not placement:
        return _check("beats_baseline", label, criterion, INSUFFICIENT, "n/a", "The real run's return could not be placed.")
    actual = f"{placement['percentile']:.0f}th percentile of {placement['n']} random runs"
    detail = baseline.get("caveat")
    if seeds < MIN_SEEDS_FOR_READING:
        return _check("beats_baseline", label, criterion, INSUFFICIENT, actual, f"Fewer than {MIN_SEEDS_FOR_READING} random runs. {detail}")
    return _check(
        "beats_baseline", label, criterion, PASS if placement["percentile"] >= criteria.baseline_percentile else FAIL, actual, detail
    )


def _banners(
    metrics: Mapping[str, Any], coverage: Mapping[str, Any] | None, criteria: Criteria
) -> list[dict[str, Any]]:
    banners = [
        {
            "key": "survivorship", "level": "warning",
            "text": "Probably optimistic: the symbols are today's index members. The run never held a company that "
            "was later dropped from the index or went under, so the results are flattered by hindsight.",
        }
    ]
    if coverage:
        achievable, live = coverage.get("achievable_points"), coverage.get("live_points_max")
        left_out = ", ".join(part["label"].lower() for part in coverage.get("inactive_parts", []))
        text = (
            f"Price-only core: only the chart-based part of the score was tested ({achievable} of the live {live} points were reachable). "
            f"Not in this run: {left_out}."
        )
        if coverage.get("bar_reachable") is False:
            text += " The confidence bar is higher than any price-only setup can reach, so no trade could have been taken."
    else:
        text = "Price-only core: only the chart-based part of the score is tested; news, options, insiders, fundamentals and the AI are not included."
    banners.append({"key": "price_only", "level": "warning", "text": text})
    n = metrics["trades"]["closed_trades"]
    low, high = metrics["trades"]["average_r_low"], metrics["trades"]["average_r_high"]
    interval = f" The 95% interval on the average R is {low:+.2f} to {high:+.2f}." if low is not None and high is not None else ""
    relation = "under" if n < criteria.min_trades else "at or over"
    banners.append(
        {
            "key": "sample_size", "level": "warning" if n < criteria.min_trades else "info",
            "text": f"{n} closed trades is {relation} the {criteria.min_trades} usually needed to read a pattern.{interval}",
        }
    )
    banners.append(
        {
            "key": "daily_bars", "level": "info",
            "text": "Fills and exits use daily bars: entries at the day's open plus slippage, and a stop or target touched later "
            "on the entry day is not seen until the next day, so results are slightly optimistic.",
        }
    )
    return banners


def build_scorecard(
    metrics: Mapping[str, Any],
    benchmarks: Mapping[str, Any] | None,
    baseline: Mapping[str, Any] | None,
    coverage: Mapping[str, Any] | None,
    params: Mapping[str, Any],
    criteria: Criteria | None = None,
) -> dict[str, Any]:
    """The checklist and banners for one run. `baseline` is baseline.read_baseline()'s output."""
    criteria = criteria or Criteria()
    checks = [
        _trades_check(metrics, criteria),
        _after_costs_check(metrics, params),
        _years_check(metrics, criteria),
        _drawdown_check(metrics, criteria),
        *_spy_checks(metrics, benchmarks),
        _baseline_check(baseline, criteria),
    ]
    counts = {status: sum(1 for c in checks if c["status"] == status) for status in (PASS, FAIL, INSUFFICIENT)}
    return {
        "note": "Informational, not a verdict. Nothing in the app acts on these lines, and a pass here does not make a strategy safe to trade.",
        "criteria": asdict(criteria),
        "checks": checks,
        "counts": {**counts, "total": len(checks)},
        "banners": _banners(metrics, coverage, criteria),
    }
