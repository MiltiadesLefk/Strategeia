"""Rule-based maths behind the earnings preview.

Everything here is computed from numbers the app already holds (past report
dates, the price history, consensus estimates, options-implied volatility).
Nothing is predicted and no price target is invented: the "bull / base / bear"
rows are positions in the stock's OWN past reaction distribution, and the
optional AI paragraph only narrates what these functions return.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from app.analysis.earnings_history_scoring import MEANINGFUL_SURPRISE_PCT, REACTION_LOOKAHEAD_DAYS
from app.data_providers.base import EarningsHistoryEntry

# Fewer past reactions than this and a percentile is just one or two events
# dressed up as a distribution, so the scenario table is left out instead.
MIN_REACTIONS_FOR_SCENARIOS = 4
# Scenario rows sit at these points of the signed past-reaction distribution.
BULL_PERCENTILE = 75
BASE_PERCENTILE = 50
BEAR_PERCENTILE = 25
# The options-implied move is called rich or cheap against the stock's own median
# earnings move when the ratio crosses these (rich: options cost more than usual).
IMPLIED_RICH_RATIO = 1.25
IMPLIED_CHEAP_RATIO = 0.8
# How many past quarters the surprise table shows.
SURPRISE_TABLE_QUARTERS = 4
# Estimates this close to (or beyond) a past beat/miss streak get a "watch" bullet.
RSI_STRETCHED_HIGH = 70.0
RSI_STRETCHED_LOW = 30.0
NEAR_52W_EXTREME_PCT = 0.05


@dataclass(frozen=True)
class Reaction:
    """What the stock did around one past report: close before to close after."""

    report_date: date
    move_pct: float  # signed


def earnings_reactions(ohlcv: pd.DataFrame, history: list[EarningsHistoryEntry]) -> list[Reaction]:
    """Signed % move around each past report, newest first.

    Same window as `historical_earnings_move_pct` (close on the last trading day
    at or before the report date vs the first close within REACTION_LOOKAHEAD_DAYS
    after it, which covers both before-open and after-close reporters), kept
    signed and per-event so a distribution can be read off it.
    """
    if ohlcv.empty or not history:
        return []
    dates = pd.to_datetime(ohlcv["date"], utc=True, errors="coerce").dt.tz_localize(None).dt.normalize()
    closes = ohlcv["close"].to_numpy()
    out: list[Reaction] = []
    for entry in history:
        report_at = pd.Timestamp(entry.date)
        before = (dates <= report_at).to_numpy().nonzero()[0]
        after = ((dates > report_at) & (dates <= report_at + timedelta(days=REACTION_LOOKAHEAD_DAYS))).to_numpy().nonzero()[0]
        if len(before) == 0 or len(after) == 0:
            continue
        before_close = float(closes[before[-1]])
        if before_close <= 0:
            continue
        out.append(Reaction(entry.date, (float(closes[after[0]]) - before_close) / before_close * 100))
    out.sort(key=lambda r: r.report_date, reverse=True)
    return out


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile (pct 0-100) of a non-empty list."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def median_abs_move(reactions: list[Reaction]) -> float | None:
    if not reactions:
        return None
    return percentile([abs(r.move_pct) for r in reactions], 50)


@dataclass(frozen=True)
class Scenario:
    name: str  # Bull | Base | Bear
    percentile: int
    move_pct: float
    description: str


def build_scenarios(reactions: list[Reaction]) -> list[Scenario]:
    """Bull / base / bear as the 75th / 50th / 25th percentile of past signed reactions.

    These are "how the stock has reacted before", not forecasts: the bull row is
    the reaction that 3 in 4 past reports did not beat, the bear row the one that
    3 in 4 did not fall below. Empty when the sample is too small to mean anything.
    """
    if len(reactions) < MIN_REACTIONS_FOR_SCENARIOS:
        return []
    moves = [r.move_pct for r in reactions]
    rows = [
        ("Bull", BULL_PERCENTILE, "A better-than-typical past reaction"),
        ("Base", BASE_PERCENTILE, "The typical past reaction"),
        ("Bear", BEAR_PERCENTILE, "A worse-than-typical past reaction"),
    ]
    return [Scenario(name, pct, round(percentile(moves, pct), 2), text) for name, pct, text in rows]


@dataclass(frozen=True)
class ImpliedVsHistory:
    implied_move_pct: float
    historical_median_move_pct: float
    ratio: float
    verdict: str  # rich | cheap | in line


def implied_vs_history(implied_move_pct: float | None, historical_median: float | None) -> ImpliedVsHistory | None:
    if not implied_move_pct or not historical_median or historical_median <= 0:
        return None
    ratio = implied_move_pct / historical_median
    verdict = "rich" if ratio >= IMPLIED_RICH_RATIO else "cheap" if ratio <= IMPLIED_CHEAP_RATIO else "in line"
    return ImpliedVsHistory(round(implied_move_pct, 2), round(historical_median, 2), round(ratio, 2), verdict)


@dataclass(frozen=True)
class TrackRecord:
    quarters: int  # quarters with a usable surprise figure
    beats: int
    misses: int
    in_line: int
    average_surprise_pct: float | None


def surprise_track_record(history: list[EarningsHistoryEntry]) -> TrackRecord:
    usable = [e for e in history if e.surprise_pct is not None]
    beats = sum(1 for e in usable if e.surprise_pct >= MEANINGFUL_SURPRISE_PCT)
    misses = sum(1 for e in usable if e.surprise_pct <= -MEANINGFUL_SURPRISE_PCT)
    average = sum(e.surprise_pct for e in usable) / len(usable) if usable else None
    return TrackRecord(len(usable), beats, misses, len(usable) - beats - misses, average)


def watch_points(
    *,
    days_until: int | None,
    track: TrackRecord,
    verdict: ImpliedVsHistory | None,
    implied_covers_earnings: bool,
    rsi14: float | None,
    trend: str | None,
    price: float | None,
    week52_high: float | None,
    week52_low: float | None,
    eps_estimate: float | None,
    last_eps_actual: float | None,
) -> list[str]:
    """Short plain-English "what to watch" bullets, each derived from a number
    in the preview. A bullet appears only when its data exists and its condition
    holds; none is a generic checklist item."""
    points: list[str] = []
    if verdict is not None and implied_covers_earnings:
        if verdict.verdict == "rich":
            points.append(
                f"Options price a {verdict.implied_move_pct:.1f}% move, {verdict.ratio:.1f}x this stock's median "
                f"earnings move ({verdict.historical_median_move_pct:.1f}%): a reaction near its usual size would "
                "land inside what options already expect."
            )
        elif verdict.verdict == "cheap":
            points.append(
                f"Options price a {verdict.implied_move_pct:.1f}% move, only {verdict.ratio:.1f}x its median "
                f"earnings move ({verdict.historical_median_move_pct:.1f}%): a typical reaction would exceed what "
                "options expect."
            )
    if track.quarters >= MIN_REACTIONS_FOR_SCENARIOS:
        if track.beats / track.quarters >= 0.75:
            points.append(
                f"It beat consensus EPS in {track.beats} of the last {track.quarters} quarters, so a plain beat "
                "is already the habit; the market tends to look at guidance instead."
            )
        elif track.misses / track.quarters >= 0.5:
            points.append(f"It missed consensus EPS in {track.misses} of the last {track.quarters} quarters.")
    if eps_estimate is not None and last_eps_actual is not None and last_eps_actual != 0:
        change = (eps_estimate - last_eps_actual) / abs(last_eps_actual) * 100
        points.append(
            f"The EPS estimate ({eps_estimate:.2f}) is {abs(change):.0f}% {'above' if change >= 0 else 'below'} "
            f"the last reported EPS ({last_eps_actual:.2f}); check whether guidance supports that step."
        )
    if rsi14 is not None and rsi14 >= RSI_STRETCHED_HIGH:
        points.append(f"RSI is {rsi14:.0f}: the stock is stretched into the report, so good news may be priced in.")
    elif rsi14 is not None and rsi14 <= RSI_STRETCHED_LOW:
        points.append(f"RSI is {rsi14:.0f}: the stock is oversold into the report, so bad news may be priced in.")
    if price and week52_high and price >= week52_high * (1 - NEAR_52W_EXTREME_PCT):
        points.append("It trades within 5% of its 52-week high going into the print.")
    elif price and week52_low and price <= week52_low * (1 + NEAR_52W_EXTREME_PCT):
        points.append("It trades within 5% of its 52-week low going into the print.")
    if days_until is not None and days_until <= 1 and trend:
        points.append(f"The report is {'today' if days_until == 0 else 'tomorrow'}; the chart trend is {trend.lower()}.")
    return points
