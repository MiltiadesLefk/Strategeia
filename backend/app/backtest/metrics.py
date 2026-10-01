"""Performance statistics for a finished backtest, computed from its stored rows.

Pure functions: they take a run's daily equity points and its trades and return
plain numbers, with no database, no network and no randomness except the seeded
bootstrap. Nothing is stored; the numbers are recomputed on every read, so they
can never disagree with the rows they describe.

Conventions (stated once here, repeated in the response so the page can show them)
---------------------------------------------------------------------------------
* A daily return is equity at one close over equity at the previous close. The
  first day's return is measured from the starting cash, so a run's returns
  compound exactly to its total return.
* Annualisation uses 252 trading days a year. The risk-free rate is 0, so Sharpe
  and Sortino are plain return-over-risk ratios, not excess-return ratios.
* Sharpe = mean(daily return) / sample standard deviation x sqrt(252).
  Sortino = mean(daily return) / downside deviation x sqrt(252), where the
  downside deviation is the root mean square of the negative returns (target 0)
  over ALL days.
* CAGR = (final / start) ^ (365.25 / calendar days) - 1. A run shorter than
  MIN_CALENDAR_DAYS_TO_ANNUALISE calendar days gets no CAGR, Calmar or trades per
  year: stretching three weeks into a year says nothing real.
* Drawdown is measured on daily closing equity against the highest value so far
  (the starting cash counts as the first peak). It understates the true intraday
  drawdown.
* A trade "wins" when its realised P&L (after slippage and commission) is above
  zero. Trades still open when the run ended are valued at the last close and
  left out of every trade statistic except the exit-reason mix.
* Win rate carries a Wilson interval and average R a seeded bootstrap interval
  (app.portfolio.signal_stats), so a small sample cannot pass for a result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from app.portfolio.signal_stats import bootstrap_mean_interval, wilson_interval

TRADING_DAYS_PER_YEAR = 252
RISK_FREE_RATE = 0.0
MIN_CALENDAR_DAYS_TO_ANNUALISE = 90
# A standard deviation below this is "no variation" (flat equity), not a tiny
# denominator that would turn float noise into a huge ratio.
MIN_STDEV = 1e-12
# A calendar year/month shorter than this many days at the edge of a run is
# labelled partial.
YEAR_START_SLACK_DAYS = 7
YEAR_END_SLACK_DAYS = 7
MONTH_EDGE_SLACK_DAYS = 3

CONVENTIONS = {
    "trading_days_per_year": TRADING_DAYS_PER_YEAR,
    "risk_free_rate": RISK_FREE_RATE,
    "returns": "daily closing equity over the previous close; the first day is measured from the starting cash",
    "sharpe": "mean daily return / sample standard deviation x sqrt(252), risk-free rate 0",
    "sortino": "mean daily return / downside deviation (negative returns, target 0) x sqrt(252)",
    "cagr": f"(final / start) ^ (365.25 / calendar days) - 1; not shown for runs under {MIN_CALENDAR_DAYS_TO_ANNUALISE} calendar days",
    "drawdown": "from the highest daily close so far (the starting cash is the first peak); intraday lows are not seen",
    "win": "realised P&L after costs above zero; trades still open at the end are left out",
    "intervals": "Wilson 95% interval on the win rate, seeded bootstrap 95% interval on the average R",
}


# ---------------------------------------------------------------- inputs


@dataclass(frozen=True)
class EquityRow:
    day: date
    equity: float
    open_positions: int = 0


@dataclass(frozen=True)
class TradeRow:
    symbol: str
    direction: str
    status: str  # "closed" or "open" (held when the run ended)
    entry_date: date
    exit_date: date | None
    realized_pnl: float | None
    realized_r: float | None
    holding_days: int | None
    close_reason: str | None
    fees_paid: float | None = None


def _read(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def equity_rows(items: Iterable[Any]) -> list[EquityRow]:
    """Equity points from stored rows or runner dicts, oldest first."""
    rows = [
        EquityRow(_read(i, "day"), float(_read(i, "equity")), int(_read(i, "open_positions", 0) or 0)) for i in items
    ]
    rows.sort(key=lambda r: r.day)
    return rows


def trade_rows(items: Iterable[Any]) -> list[TradeRow]:
    return [
        TradeRow(
            symbol=_read(i, "symbol"),
            direction=_read(i, "direction"),
            status=_read(i, "status", "closed"),
            entry_date=_read(i, "entry_date"),
            exit_date=_read(i, "exit_date"),
            realized_pnl=_read(i, "realized_pnl"),
            realized_r=_read(i, "realized_r"),
            holding_days=_read(i, "holding_days"),
            close_reason=_read(i, "close_reason"),
            fees_paid=_read(i, "fees_paid"),
        )
        for i in items
    ]


# ---------------------------------------------------------------- returns and risk


def daily_returns(starting_cash: float, equity: Sequence[EquityRow]) -> np.ndarray:
    """One return per equity point, the first from the starting cash."""
    if not equity or starting_cash <= 0:
        return np.array([], dtype=float)
    values = np.array([starting_cash, *[row.equity for row in equity]], dtype=float)
    previous = values[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        returns = np.where(previous > 0, values[1:] / previous - 1.0, 0.0)
    return returns


def sharpe_ratio(returns: np.ndarray) -> float | None:
    if len(returns) < 2:
        return None
    std = float(np.std(returns, ddof=1))
    if std < MIN_STDEV:
        return None
    return float((np.mean(returns) - RISK_FREE_RATE / TRADING_DAYS_PER_YEAR) / std * math.sqrt(TRADING_DAYS_PER_YEAR))


def sortino_ratio(returns: np.ndarray) -> float | None:
    if len(returns) < 2:
        return None
    downside = float(np.sqrt(np.mean(np.minimum(returns, 0.0) ** 2)))
    if downside < MIN_STDEV:
        return None
    return float(np.mean(returns) / downside * math.sqrt(TRADING_DAYS_PER_YEAR))


def annualised_volatility_pct(returns: np.ndarray) -> float | None:
    if len(returns) < 2:
        return None
    return float(np.std(returns, ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100)


def years_between(first: date, last: date) -> float:
    """Calendar span in years, counting both end days (a one-day run is one day)."""
    return ((last - first).days + 1) / 365.25


def cagr_pct(starting_cash: float, final_equity: float, first: date, last: date) -> float | None:
    if starting_cash <= 0 or final_equity <= 0:
        return None
    if (last - first).days + 1 < MIN_CALENDAR_DAYS_TO_ANNUALISE:
        return None
    return float(((final_equity / starting_cash) ** (1 / years_between(first, last)) - 1) * 100)


def drawdown_curve(starting_cash: float, equity: Sequence[EquityRow]) -> list[dict[str, Any]]:
    """Per day: how far below the highest close so far, in percent (0 or negative)."""
    peak = starting_cash
    out: list[dict[str, Any]] = []
    for row in equity:
        peak = max(peak, row.equity)
        out.append({"day": row.day, "drawdown_pct": (row.equity / peak - 1) * 100 if peak > 0 else 0.0})
    return out


def drawdown_summary(starting_cash: float, equity: Sequence[EquityRow]) -> dict[str, Any]:
    """The worst drawdown (depth, when it started, bottomed and recovered) and the
    longest stretch spent below a previous high, in trading days."""
    empty = {
        "max_drawdown_pct": 0.0, "peak_date": None, "trough_date": None, "recovery_date": None,
        "max_drawdown_days": 0, "longest_underwater_days": 0, "underwater_now": False,
    }
    if not equity:
        return empty
    peak_value, peak_index = starting_cash, -1  # -1: the starting cash, before the first day
    worst, worst_peak_index, worst_trough_index = 0.0, -1, -1
    underwater_run, longest_underwater = 0, 0
    for i, row in enumerate(equity):
        if row.equity >= peak_value:
            peak_value, peak_index = row.equity, i
            underwater_run = 0
        else:
            underwater_run += 1
            longest_underwater = max(longest_underwater, underwater_run)
        depth = (peak_value - row.equity) / peak_value if peak_value > 0 else 0.0
        if depth > worst:
            worst, worst_peak_index, worst_trough_index = depth, peak_index, i
    if worst == 0.0:
        return {**empty, "longest_underwater_days": longest_underwater}
    # Recovery: the first day after the trough that closes at or above the peak it fell from.
    peak_level = starting_cash if worst_peak_index < 0 else equity[worst_peak_index].equity
    recovery_index = next((j for j in range(worst_trough_index + 1, len(equity)) if equity[j].equity >= peak_level), None)
    end_index = recovery_index if recovery_index is not None else len(equity) - 1
    return {
        "max_drawdown_pct": worst * 100,
        "peak_date": equity[worst_peak_index].day if worst_peak_index >= 0 else None,
        "trough_date": equity[worst_trough_index].day,
        "recovery_date": equity[recovery_index].day if recovery_index is not None else None,
        "max_drawdown_days": end_index - worst_peak_index,
        "longest_underwater_days": longest_underwater,
        "underwater_now": equity[-1].equity < peak_value,
    }


# ---------------------------------------------------------------- calendar tables


def _period_returns(starting_cash: float, equity: Sequence[EquityRow], key) -> list[dict[str, Any]]:
    """Return per calendar period: last close in the period over the last close of
    the one before (the starting cash for the first)."""
    out: list[dict[str, Any]] = []
    previous_end = starting_cash
    index = 0
    while index < len(equity):
        period = key(equity[index].day)
        end = index
        while end + 1 < len(equity) and key(equity[end + 1].day) == period:
            end += 1
        last_equity = equity[end].equity
        out.append(
            {
                "period": period,
                "first_day": equity[index].day,
                "last_day": equity[end].day,
                "days": end - index + 1,
                "return_pct": (last_equity / previous_end - 1) * 100 if previous_end > 0 else 0.0,
            }
        )
        previous_end = last_equity
        index = end + 1
    return out


def yearly_returns(starting_cash: float, equity: Sequence[EquityRow]) -> list[dict[str, Any]]:
    rows = _period_returns(starting_cash, equity, lambda d: d.year)
    out = []
    for position, row in enumerate(rows):
        first, last = row["first_day"], row["last_day"]
        # Partial: the run began after the first days of January or ended before the last days of December.
        starts_late = position == 0 and (first.month, first.day) > (1, YEAR_START_SLACK_DAYS)
        ends_early = position == len(rows) - 1 and (last.month, last.day) < (12, 31 - YEAR_END_SLACK_DAYS)
        out.append(
            {"year": row["period"], "return_pct": row["return_pct"], "days": row["days"],
             "first_day": first, "last_day": last, "partial": bool(starts_late or ends_early)}
        )
    return out


def monthly_returns(starting_cash: float, equity: Sequence[EquityRow]) -> list[dict[str, Any]]:
    rows = _period_returns(starting_cash, equity, lambda d: (d.year, d.month))
    out = []
    for position, row in enumerate(rows):
        year, month = row["period"]
        first, last = row["first_day"], row["last_day"]
        starts_late = position == 0 and first.day > MONTH_EDGE_SLACK_DAYS + 1
        ends_early = position == len(rows) - 1 and (last.day < 28 - MONTH_EDGE_SLACK_DAYS)
        out.append(
            {"year": year, "month": month, "return_pct": row["return_pct"], "days": row["days"],
             "partial": bool(starts_late or ends_early)}
        )
    return out


# ---------------------------------------------------------------- trades


def _trade_block(trades: Sequence[TradeRow]) -> dict[str, Any]:
    """Statistics over CLOSED trades."""
    pnls = [float(t.realized_pnl or 0.0) for t in trades]
    rs = [float(t.realized_r) for t in trades if t.realized_r is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n = len(trades)
    wilson = wilson_interval(len(wins), n)
    r_interval = bootstrap_mean_interval(rs)
    gross_profit = sum(wins)
    gross_loss = -sum(p for p in pnls if p < 0)
    win_rs = [t.realized_r for t in trades if t.realized_r is not None and (t.realized_pnl or 0) > 0]
    loss_rs = [t.realized_r for t in trades if t.realized_r is not None and (t.realized_pnl or 0) <= 0]
    average_win = sum(wins) / len(wins) if wins else None
    average_loss = sum(losses) / len(losses) if losses else None
    return {
        "closed_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": len(wins) / n * 100 if n else None,
        "win_rate_low_pct": wilson[0] * 100 if wilson else None,
        "win_rate_high_pct": wilson[1] * 100 if wilson else None,
        "average_r": sum(rs) / len(rs) if rs else None,
        "average_r_low": r_interval[0] if r_interval else None,
        "average_r_high": r_interval[1] if r_interval else None,
        "expectancy_usd": sum(pnls) / n if n else None,
        "total_pnl": sum(pnls),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        # None when there is no losing trade (the ratio is unbounded) or no trades at all.
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "average_win": average_win,
        "average_loss": average_loss,
        "average_win_r": sum(win_rs) / len(win_rs) if win_rs else None,
        "average_loss_r": sum(loss_rs) / len(loss_rs) if loss_rs else None,
        "payoff_ratio": average_win / abs(average_loss) if average_win is not None and average_loss not in (None, 0) else None,
    }


def longest_losing_streak(trades: Sequence[TradeRow]) -> int:
    """Most consecutive closed trades that did not win, in the order they closed."""
    ordered = sorted(trades, key=lambda t: (t.exit_date or date.max, t.entry_date))
    longest = run = 0
    for trade in ordered:
        if (trade.realized_pnl or 0) > 0:
            run = 0
        else:
            run += 1
            longest = max(longest, run)
    return longest


def exit_reason_mix(trades: Sequence[TradeRow]) -> list[dict[str, Any]]:
    """How trades ended, over every trade including those still open at the end."""
    groups: dict[str, list[TradeRow]] = {}
    for trade in trades:
        groups.setdefault(trade.close_reason or "unknown", []).append(trade)
    total = len(trades)
    out = []
    for reason, group in sorted(groups.items(), key=lambda item: -len(item[1])):
        rs = [t.realized_r for t in group if t.realized_r is not None]
        out.append(
            {
                "reason": reason,
                "count": len(group),
                "share_pct": len(group) / total * 100 if total else 0.0,
                "average_r": sum(rs) / len(rs) if rs else None,
                "total_pnl": sum(float(t.realized_pnl or 0.0) for t in group),
            }
        )
    return out


def direction_split(trades: Sequence[TradeRow]) -> list[dict[str, Any]]:
    """Long versus short, over closed trades."""
    out = []
    for direction in ("long", "short"):
        group = [t for t in trades if t.direction == direction]
        block = _trade_block(group)
        out.append(
            {
                "direction": direction,
                "trades": block["closed_trades"],
                "share_pct": len(group) / len(trades) * 100 if trades else 0.0,
                "win_rate_pct": block["win_rate_pct"],
                "average_r": block["average_r"],
                "total_pnl": block["total_pnl"],
            }
        )
    return out


# ---------------------------------------------------------------- the whole report


def compute_metrics(
    equity: Sequence[EquityRow], trades: Sequence[TradeRow], starting_cash: float, *, include_series: bool = True
) -> dict[str, Any]:
    """Every statistic for one run. `starting_cash` is the account the run began with.
    Degenerate input (no equity points, no trades, flat equity) gives None for the
    statistics that are undefined, never a made-up zero."""
    closed = [t for t in trades if t.status == "closed"]
    returns = daily_returns(starting_cash, equity)
    first = equity[0].day if equity else None
    last = equity[-1].day if equity else None
    final_equity = equity[-1].equity if equity else starting_cash
    calendar_days = ((last - first).days + 1) if first and last else 0
    annualise = calendar_days >= MIN_CALENDAR_DAYS_TO_ANNUALISE
    years = years_between(first, last) if first and last else None
    cagr = cagr_pct(starting_cash, final_equity, first, last) if first and last else None
    drawdown = drawdown_summary(starting_cash, equity)
    max_dd = drawdown["max_drawdown_pct"]
    holding = [t.holding_days for t in closed if t.holding_days is not None]
    trade_block = _trade_block(closed)
    exposure_days = sum(1 for row in equity if row.open_positions >= 1)

    out: dict[str, Any] = {
        "conventions": CONVENTIONS,
        "period": {
            "first_day": first, "last_day": last, "calendar_days": calendar_days,
            "trading_days": len(equity), "years": years, "annualised": annualise,
        },
        "returns": {
            "starting_equity": starting_cash,
            "final_equity": final_equity,
            "total_return_pct": (final_equity / starting_cash - 1) * 100 if starting_cash > 0 else None,
            "cagr_pct": cagr,
            "volatility_pct": annualised_volatility_pct(returns),
            "sharpe": sharpe_ratio(returns),
            "sortino": sortino_ratio(returns),
            "calmar": (cagr / max_dd) if cagr is not None and max_dd > 0 else None,
            "best_day_pct": float(returns.max() * 100) if len(returns) else None,
            "worst_day_pct": float(returns.min() * 100) if len(returns) else None,
        },
        "drawdown": drawdown,
        "trades": {
            **trade_block,
            "open_at_end": len(trades) - len(closed),
            "trades_per_year": len(closed) / years if annualise and years else None,
            "average_holding_days": sum(holding) / len(holding) if holding else None,
            "longest_losing_streak": longest_losing_streak(closed),
            "total_fees": sum(float(t.fees_paid or 0.0) for t in closed),
        },
        "exposure": {
            "days_with_a_position": exposure_days,
            "exposure_pct": exposure_days / len(equity) * 100 if equity else None,
            "average_open_positions": sum(r.open_positions for r in equity) / len(equity) if equity else None,
        },
        "yearly_returns": yearly_returns(starting_cash, equity),
        "monthly_returns": monthly_returns(starting_cash, equity),
        "exit_reasons": exit_reason_mix(trades),
        "by_direction": direction_split(closed),
    }
    if include_series:
        out["drawdown_series"] = drawdown_curve(starting_cash, equity)
    return out


def headline(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """The few numbers a baseline seed keeps (see baseline.py)."""
    returns, trades, drawdown = metrics["returns"], metrics["trades"], metrics["drawdown"]
    return {
        "total_return_pct": returns["total_return_pct"],
        "sharpe": returns["sharpe"],
        "average_r": trades["average_r"],
        "win_rate_pct": trades["win_rate_pct"],
        "trade_count": trades["closed_trades"],
        "max_drawdown_pct": drawdown["max_drawdown_pct"],
    }
