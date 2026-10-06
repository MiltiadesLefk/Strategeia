"""Best/worst-price (MFE / MAE) figures for positions the engine did not record
itself: live numbers for OPEN positions, and a backfill for CLOSED ones that
predate the feature. The maths is in portfolio/excursion.py; this module only
decides which bars a position lived through.

Nothing here runs by itself. The live figure is computed on the fly by the
positions endpoint (a GET, so it never writes), and the backfill is a function
you call on purpose (see backfill_excursions): it rewrites columns on closed
rows and is never triggered automatically against anyone's data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from sqlmodel import Session, select

from app.data_providers.base import AllProvidersFailedError, DataProvider
from app.markets import is_daily_bar_final
from app.portfolio.engine import EXIT_SCAN_PERIOD, PaperTradingEngine
from app.portfolio.excursion import Excursion, LastBar, compute_excursion
from app.portfolio.models import PaperPosition
from app.timeutil import utcnow_naive

# A time exit fills at its bar's close, so the bar it happened on is found by
# matching that close. Slippage moves the fill a few basis points off the close;
# 1% is wide enough for any sane slippage setting and tight enough that a bar
# that is clearly not the exit bar is rejected (the row is then skipped, not guessed).
TIME_EXIT_CLOSE_TOLERANCE = 0.01

# yfinance-style history periods, shortest first, with how many calendar days of
# history each reliably covers. The backfill asks for the shortest one that still
# reaches back past a position's entry bar. Anything older than the last one is skipped.
_PERIODS: tuple[tuple[int, str], ...] = (
    (80, "3mo"),
    (170, "6mo"),
    (350, "1y"),
    (700, "2y"),
    (1800, "5y"),
)


def _naive_utc_dates(bars: pd.DataFrame) -> pd.Series:
    """Same date reading as PaperTradingEngine._bars_after_entry."""
    return pd.to_datetime(bars["date"], utc=True, errors="coerce").dt.tz_localize(None)


def live_excursion(position: PaperPosition, data_provider: DataProvider) -> Excursion | None:
    """MFE / MAE so far of an OPEN position, from the bars since its entry bar.
    Read-only: nothing is stored. None when the bars can't be had, or when the
    entry bar is outside the history window (the early part of the trade would be
    missing, so the range would be understated)."""
    try:
        bars = data_provider.get_ohlcv(position.symbol, period=EXIT_SCAN_PERIOD, interval="1d")
    except AllProvidersFailedError:
        return None
    if bars.empty or not PaperTradingEngine._entry_bar_in_window(bars, position.opened_at):
        return None
    held = PaperTradingEngine._bars_after_entry(bars, position.opened_at)
    # Every bar so far, today's still-forming one included: its high and low to
    # date have already happened while the position was held.
    return compute_excursion(position.direction, position.entry_price, position.stop_loss, held, last_bar="complete")


def _touched(position: PaperPosition, bar: pd.Series) -> tuple[bool, bool]:
    """(stop touched, TP1 touched) on this bar, the same tests the exit scan uses."""
    high, low = float(bar["high"]), float(bar["low"])
    if position.direction == "long":
        return low <= position.stop_loss, high >= position.tp1
    return high >= position.stop_loss, low <= position.tp1


def held_window(position: PaperPosition, bars: pd.DataFrame) -> tuple[pd.DataFrame, LastBar] | None:
    """The bars a CLOSED position lived through, and how its last bar relates to
    the exit, rebuilt from finished bars. None when that can't be established
    honestly (entry bar not in `bars`, undated bars, an exit we can't locate).

    A position is often closed later than the exit really happened (the scan only
    runs every so often), so `closed_at` is not the exit bar for a stop or target:
    those are located by re-walking the bars with the exit scan's own touch tests."""
    if position.closed_at is None or position.close_price is None or bars.empty or "date" not in bars.columns:
        return None
    if not PaperTradingEngine._entry_bar_in_window(bars, position.opened_at):
        return None
    scope = PaperTradingEngine._bars_after_entry(bars, position.opened_at)
    if not scope.empty:
        scope = scope[_naive_utc_dates(scope) <= pd.Timestamp(position.closed_at)]
    reason = position.close_reason
    if position.partial_fill_price is not None or reason == "tp2_hit":
        return None  # a scaled-out runner: the re-walk below only knows the original levels, so it would guess

    if reason in ("stop_hit", "tp1_hit"):
        for number, (_, bar) in enumerate(scope.iterrows(), start=1):
            stop_touched, tp_touched = _touched(position, bar)
            if not (stop_touched or tp_touched):
                continue
            # The scan checks the stop first, so a TP1 exit bar never touched the stop.
            located = "stop_hit" if stop_touched else "tp1_hit"
            return (scope.iloc[:number], "exit") if located == reason else None
        return None  # the level was never touched in these bars: the data disagrees, don't guess

    def final(bar: pd.Series) -> bool:
        try:
            day = pd.Timestamp(bar["date"]).date()
        except (KeyError, ValueError, TypeError):
            return False
        return is_daily_bar_final(position.symbol, day, position.closed_at)

    if reason == "time_exit":
        # A time exit fires on a finished bar, at its close.
        finished = [i for i, (_, bar) in enumerate(scope.iterrows()) if final(bar)]
        if not finished:
            return None
        held = scope.iloc[: finished[-1] + 1]
        last_close = float(held.iloc[-1]["close"])
        if last_close <= 0 or abs(last_close - position.close_price) / last_close > TIME_EXIT_CLOSE_TOLERANCE:
            return None
        return held, "complete"

    # Manual (or any other) close: the last bar is either finished by then, or the
    # position left part-way through it.
    if scope.empty:
        return scope, "complete"  # opened and closed within the entry bar's session
    return scope, "complete" if final(scope.iloc[-1]) else "exit"


def _period_for_age(opened_at: datetime, now: datetime) -> str | None:
    age_days = (now - opened_at).days
    for limit, period in _PERIODS:
        if age_days <= limit:
            return period
    return None


@dataclass
class BackfillSummary:
    updated: int = 0
    # Closed rows that already had figures (left alone unless overwrite=True).
    already_recorded: int = 0
    # Why rows were left with no figures: reason -> count. Never filled with a guess.
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1


def backfill_excursions(
    session: Session,
    data_provider: DataProvider,
    *,
    overwrite: bool = False,
    now: datetime | None = None,
) -> BackfillSummary:
    """Fills mfe_pct / mae_pct / mfe_r / mae_r on closed positions that have none
    (closed before the figures were recorded), from the provider's daily bars.

    Idempotent: a second run changes nothing, because rows that already have
    figures are skipped (`overwrite=True` recomputes them all). Rows whose bars or
    exit bar can't be established are left as they were and counted in `skipped`.
    Call it on purpose (a script or a shell); nothing in the app runs it."""
    now = now or utcnow_naive()
    summary = BackfillSummary()
    bars_cache: dict[tuple[str, str], pd.DataFrame | None] = {}
    closed = session.exec(select(PaperPosition).where(PaperPosition.status == "closed")).all()
    for position in closed:
        if position.mfe_pct is not None and not overwrite:
            summary.already_recorded += 1
            continue
        period = _period_for_age(position.opened_at, now)
        if period is None:
            summary.skip("older than the longest history window")
            continue
        key = (position.symbol, period)
        if key not in bars_cache:
            try:
                bars_cache[key] = data_provider.get_ohlcv(position.symbol, period=period, interval="1d")
            except AllProvidersFailedError:
                bars_cache[key] = None
        bars = bars_cache[key]
        if bars is None or bars.empty:
            summary.skip("no price history available")
            continue
        window = held_window(position, bars)
        if window is None:
            summary.skip("could not place the entry or exit in the price history")
            continue
        held, last_bar = window
        result = compute_excursion(
            position.direction, position.entry_price, position.stop_loss, held,
            exit_price=position.close_price, last_bar=last_bar,
        )
        if result is None:
            summary.skip("could not compute")
            continue
        position.mfe_pct, position.mae_pct = result.mfe_pct, result.mae_pct
        position.mfe_r, position.mae_r = result.mfe_r, result.mae_r
        session.add(position)
        summary.updated += 1
    session.commit()
    return summary
