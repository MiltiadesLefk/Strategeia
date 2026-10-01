"""Does the confidence score predict results? A report card on real closed trades.

Two questions, both answered only from closed paper positions joined to the
trade plan that opened them:

1. Calibration: group closed trades by the plan's confidence points and show,
   per group, how many trades there were, how often they won, the average R and
   the total P&L, each with an interval so a small group cannot pass for proof.
2. Information coefficient (IC): the Spearman rank correlation between a plan's
   confidence points and the trade's realised R, and the same for each score
   component stored on the plan, so "which parts of the score relate to results"
   has an answer (or an honest "not enough data").

Honesty rules this module keeps:
- Nothing is hardcoded or smoothed: every number comes from the stored trades.
- Every statistic is returned with n and its interval; below
  MIN_TRADES_FOR_READING trades the report says it is too early to read, loudly.
- Nothing is dropped silently: closed trades that cannot be analysed (no linked
  plan, no recorded R, points outside every band) are counted in `excluded`.
- Read-only: it never marks to market, never writes, and never touches prices.
  A position that has not been closed yet is simply not in the sample.

Confidence is quantised (17 reachable values, 6.25 percentage points apart), so
trades are banded by evidence POINTS out of the achievable maximum, not by
percentage; a percentage band would imply a resolution the engine does not have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session, select

from app.portfolio.models import PaperPosition, TradePlanRecord
from app.portfolio.signal_stats import (
    bootstrap_mean_interval,
    rank_correlation,
    wilson_interval,
)

# Confidence bands in evidence points (inclusive on both ends). The engine's
# default trade bar is 5 points, so ordinary plans start there, but the bar is a
# setting and a lower one must still land somewhere: the first band is open at the
# bottom. Bands are wide on purpose: with a few dozen trades, narrow bands leave
# every row with 2 or 3 trades and nothing readable. Edit the tuples to re-cut;
# they only need to be sorted, non-overlapping and cover 0..max (any trade that
# falls in a gap is counted in `excluded`, never dropped).
CONFIDENCE_BANDS: tuple[tuple[int, int], ...] = ((0, 6), (7, 8), (9, 10), (11, 16))

# Below this many trades a row or an IC is shown but flagged "too few to read
# much into" (matches the Dashboard/Portfolio win-rate note), and the verdict is
# "not enough data" rather than a finding.
MIN_TRADES_FOR_READING = 20
MIN_TRADES_PER_BAND = 20

# A relationship is reported as a finding only when its permutation p-value is
# below this.
SIGNIFICANCE_LEVEL = 0.05

# Score components stored on every plan: (field on TradePlanRecord, label).
# Order is the order the evidence is gathered in trade_plan_service.
SCORE_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("technical_score", "Technical"),
    ("fundamental_score", "Fundamentals"),
    ("news_score", "News"),
    ("market_confirmation_score", "Market confirmation"),
    ("options_score", "Options positioning"),
    ("insider_score", "Insider buying"),
    ("earnings_surprise_score", "Earnings surprise"),
    ("ai_overlay_score", "AI overlay (penalty)"),
    ("vix_regime_score", "VIX regime (penalty)"),
    ("expected_move_score", "Expected move (penalty)"),
    ("macro_event_score", "Macro calendar (penalty)"),
)

# What a verdict can be. "not_enough_data" always wins below the minimum sample
# so a noisy number never reads as a finding.
VERDICT_NOT_ENOUGH_DATA = "not_enough_data"
VERDICT_NO_VARIATION = "no_variation"
VERDICT_NO_CLEAR_RELATIONSHIP = "no_clear_relationship"
VERDICT_POSITIVE = "positive"
VERDICT_NEGATIVE = "negative"


@dataclass
class BandStats:
    label: str
    min_points: int
    max_points: int
    n: int
    wins: int
    win_rate: float | None  # percent, 0-100, like the rest of the app
    win_rate_low: float | None  # Wilson 95% interval, percent
    win_rate_high: float | None
    avg_r: float | None
    avg_r_low: float | None  # bootstrap 95% interval
    avg_r_high: float | None
    total_pnl: float
    small_sample: bool


@dataclass
class IcResult:
    label: str
    n: int
    ic: float | None
    p_value: float | None
    ci_low: float | None
    ci_high: float | None
    verdict: str
    note: str
    # For score components: how many of the n trades had a non-zero value, since
    # a penalty that is almost always 0 has little to correlate with.
    n_nonzero: int | None = None
    # Component rows are tested together, so a lone p < 0.05 among eleven is
    # expected by luck; this is the Bonferroni-adjusted value the verdict uses.
    p_value_adjusted: float | None = None
    key: str = ""


@dataclass
class Exclusions:
    no_linked_plan: int = 0
    missing_r: int = 0
    outside_bands: int = 0

    @property
    def total(self) -> int:
        return self.no_linked_plan + self.missing_r + self.outside_bands


@dataclass
class CalibrationReport:
    closed_trades: int
    analyzed_trades: int
    excluded: Exclusions
    points_max: int
    min_trades_for_reading: int
    min_trades_per_band: int
    reliable: bool
    headline: str
    bands: list[BandStats]
    overall: BandStats | None
    ic: IcResult
    ic_by_direction: list[IcResult]
    components: list[IcResult]
    components_tested: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class _Trade:
    points: int
    r: float
    pnl: float
    direction: str
    plan: TradePlanRecord


def confidence_points(plan: TradePlanRecord, points_max: int) -> int:
    """The evidence points behind a plan's stored confidence percentage. The
    plan stores only the percentage, which is points / points_max rounded, so
    the inverse is exact for every reachable value."""
    return max(0, min(points_max, round(plan.confidence_score * points_max / 100)))


def band_label(low: int, high: int, points_max: int) -> str:
    if low <= 0:
        return f"{high} pts or fewer"
    if high >= points_max:
        return f"{low}+ pts"
    return f"{low}-{high} pts" if high > low else f"{low} pts"


def _band_for(points: int) -> int | None:
    for index, (low, high) in enumerate(CONFIDENCE_BANDS):
        if low <= points <= high:
            return index
    return None


def summarize(label: str, low: int, high: int, trades: list[_Trade]) -> BandStats:
    n = len(trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    wilson = wilson_interval(wins, n)
    r_values = [t.r for t in trades]
    interval = bootstrap_mean_interval(r_values)
    return BandStats(
        label=label,
        min_points=low,
        max_points=high,
        n=n,
        wins=wins,
        win_rate=wins / n * 100 if n else None,
        win_rate_low=wilson[0] * 100 if wilson else None,
        win_rate_high=wilson[1] * 100 if wilson else None,
        avg_r=sum(r_values) / n if n else None,
        avg_r_low=interval[0] if interval else None,
        avg_r_high=interval[1] if interval else None,
        total_pnl=sum(t.pnl for t in trades),
        small_sample=n < MIN_TRADES_PER_BAND,
    )


def _ic_result(
    label: str,
    scores: list[float],
    results: list[float],
    *,
    key: str = "",
    adjust_for: int = 1,
) -> IcResult:
    """One IC with its verdict. `adjust_for` is how many sibling tests share the
    table (Bonferroni); 1 means no adjustment."""
    n = len(scores)
    rc = rank_correlation(scores, results)
    n_nonzero = sum(1 for s in scores if s != 0)
    p_adjusted = min(1.0, rc.p_value * adjust_for) if rc.p_value is not None else None

    if n < 2:
        verdict = VERDICT_NOT_ENOUGH_DATA
        note = "Fewer than 2 closed trades."
    elif rc.distinct_x < 2 or rc.distinct_y < 2:
        verdict = VERDICT_NO_VARIATION
        what = "The score" if rc.distinct_x < 2 else "The realised R"
        note = f"{what} took the same value on every trade, so no relationship can be measured."
    elif n < MIN_TRADES_FOR_READING:
        verdict = VERDICT_NOT_ENOUGH_DATA
        note = f"Only {n} trades; at least {MIN_TRADES_FOR_READING} are needed before this means anything."
    elif p_adjusted is not None and p_adjusted < SIGNIFICANCE_LEVEL and rc.ci_high is not None:
        verdict = VERDICT_POSITIVE if (rc.rho or 0) > 0 else VERDICT_NEGATIVE
        note = "Unlikely to be luck at the 5% level."
    else:
        verdict = VERDICT_NO_CLEAR_RELATIONSHIP
        note = "Could easily be luck: the interval includes zero or the p-value is not small."
    return IcResult(
        label=label,
        n=n,
        ic=rc.rho,
        p_value=rc.p_value,
        ci_low=rc.ci_low,
        ci_high=rc.ci_high,
        verdict=verdict,
        note=note,
        n_nonzero=n_nonzero,
        p_value_adjusted=p_adjusted if adjust_for > 1 else None,
        key=key,
    )


def _collect(session: Session, points_max: int) -> tuple[int, list[_Trade], Exclusions]:
    closed = session.exec(select(PaperPosition).where(PaperPosition.status == "closed")).all()
    plan_ids = {p.trade_plan_id for p in closed if p.trade_plan_id is not None}
    plans = (
        {pl.id: pl for pl in session.exec(select(TradePlanRecord).where(TradePlanRecord.id.in_(plan_ids))).all()}
        if plan_ids
        else {}
    )
    excluded = Exclusions()
    trades: list[_Trade] = []
    for position in closed:
        plan: Optional[TradePlanRecord] = plans.get(position.trade_plan_id) if position.trade_plan_id else None
        if plan is None:
            excluded.no_linked_plan += 1
            continue
        if position.realized_r is None:
            excluded.missing_r += 1
            continue
        points = confidence_points(plan, points_max)
        if _band_for(points) is None:
            excluded.outside_bands += 1
            continue
        trades.append(
            _Trade(
                points=points,
                r=float(position.realized_r),
                pnl=float(position.realized_pnl or 0.0),
                direction=position.direction,
                plan=plan,
            )
        )
    return len(closed), trades, excluded


def _headline(closed: int, analyzed: int, excluded: Exclusions) -> str:
    if closed == 0:
        return "No closed trades yet, so there is nothing to calibrate. Close some paper trades first."
    parts: list[str] = []
    if analyzed < MIN_TRADES_FOR_READING:
        parts.append(
            f"Only {analyzed} closed trade{'s' if analyzed != 1 else ''} can be analysed so far. "
            f"Treat every number below as noise, not a finding: at least {MIN_TRADES_FOR_READING} are needed, "
            "and many more before a pattern is trustworthy."
        )
    else:
        parts.append(
            f"{analyzed} closed trades analysed. That is enough to start looking, not enough to be sure: "
            "the intervals show how much each number could move."
        )
    if excluded.total:
        parts.append(
            f"{excluded.total} of {closed} closed trades were left out ("
            + ", ".join(
                f"{count} {reason}"
                for count, reason in (
                    (excluded.no_linked_plan, "with no linked plan"),
                    (excluded.missing_r, "with no recorded R"),
                    (excluded.outside_bands, "outside every confidence band"),
                )
                if count
            )
            + ")."
        )
    return " ".join(parts)


def compute_calibration(session: Session, points_max: int | None = None) -> CalibrationReport:
    """Build the report from the database. `points_max` defaults to the
    engine's MAX_SCORE_FOR_CONFIDENCE (imported lazily: that module pulls in the
    whole evaluation pipeline, which a pure statistics test should not need)."""
    if points_max is None:
        from app.services.trade_plan_service import MAX_SCORE_FOR_CONFIDENCE

        points_max = MAX_SCORE_FOR_CONFIDENCE

    closed_count, trades, excluded = _collect(session, points_max)

    bands: list[BandStats] = []
    for index, (low, high) in enumerate(CONFIDENCE_BANDS):
        in_band = [t for t in trades if _band_for(t.points) == index]
        bands.append(summarize(band_label(low, high, points_max), low, high, in_band))
    overall = summarize("All analysed trades", 0, points_max, trades) if trades else None

    rs = [t.r for t in trades]
    ic = _ic_result("Confidence points vs realised R", [float(t.points) for t in trades], rs, key="confidence_points")

    by_direction = [
        _ic_result(
            f"{direction.capitalize()} trades only",
            [float(t.points) for t in trades if t.direction == direction],
            [t.r for t in trades if t.direction == direction],
            key=direction,
        )
        for direction in ("long", "short")
    ]

    # Each component's n is only the trades whose plan recorded it: rows written
    # before a component existed read back None and are not zeros.
    component_inputs: list[tuple[str, str, list[float], list[float]]] = []
    for field_name, label in SCORE_COMPONENTS:
        pairs = [(float(getattr(t.plan, field_name)), t.r) for t in trades if getattr(t.plan, field_name) is not None]
        component_inputs.append((field_name, label, [p[0] for p in pairs], [p[1] for p in pairs]))
    # Bonferroni over the components that can be tested at all.
    testable = [
        item
        for item in component_inputs
        if len(item[2]) >= 2 and len(set(item[2])) >= 2 and len(set(item[3])) >= 2
    ]
    k = max(1, len(testable))
    components = [_ic_result(label, xs, ys, key=name, adjust_for=k) for name, label, xs, ys in component_inputs]

    notes = [
        "Confidence is the plan's evidence points out of "
        f"{points_max}, so trades are banded by points, not by percentage.",
        f"The {k} score components are tested together, so their p-values are adjusted for that (Bonferroni).",
    ]
    return CalibrationReport(
        closed_trades=closed_count,
        analyzed_trades=len(trades),
        excluded=excluded,
        points_max=points_max,
        min_trades_for_reading=MIN_TRADES_FOR_READING,
        min_trades_per_band=MIN_TRADES_PER_BAND,
        reliable=len(trades) >= MIN_TRADES_FOR_READING,
        headline=_headline(closed_count, len(trades), excluded),
        bands=bands,
        overall=overall,
        ic=ic,
        ic_by_direction=by_direction,
        components=components,
        components_tested=len(testable),
        notes=notes,
    )
