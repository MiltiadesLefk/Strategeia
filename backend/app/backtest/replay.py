"""Replay a settings change on the decisions already made, before switching it on.

Question answered: "if the confidence bar had been 40 instead of 30 (or the AI
objection had held a trade instead of cancelling it), which past decisions would
have gone the other way, and what did those trades earn?" The idea (re-decide
every stored decision under changed rules and compare) is from the Phil project's
replay module; the code is written for this app's own rules and rows.

What can be replayed, and why only that
---------------------------------------
Every evaluation stores the inputs of the *take / skip* decision: the confidence
points, the AI overlay's own score, verdict and stance, the direction, and the
rules version in force. From those the decision can be recomputed exactly under
different values of:

* the minimum confidence to trade;
* what an AI objection does ("cancel" a trade, "hold" it for a human, or "none");
* whether an objection costs confidence points;
* a direction filter (long only, short only).

Anything that changes the SIZE of a trade or WHEN it ends cannot be replayed from
stored rows: risk per trade, position and sector caps, holding limits, slippage,
commission, stops and targets. A different size changes cash and so every later
trade; a different exit needs the price path walked again. Those are refused with
a message that points to the Backtest Lab, which does replay prices.

How the numbers are built
-------------------------
Nothing is guessed. A decision that flips from "skipped" to "taken" is scored with
the result the missed-trades ledger already holds for it (a hypothetical trade
walked on daily bars, see portfolio/missed_trades.py), and a decision that flips
from "taken" to "skipped" loses the real closed result. A flipped trade whose
result is not known yet (still open, never computed, not simulatable) is counted
and listed but adds nothing to the win rate or R.

A decision flips only when the new settings give a DIFFERENT verdict from the
current settings do on the same stored inputs. That keeps the comparison honest
when the settings were different back then: the "before" column is what really
happened, and only decisions the change itself alters are moved.

Rows the replay cannot move (no trend, a plan the engine refused for cash or a
cap, an unknown direction, ...) keep what really happened, and are counted.

Pure and read-only: `replay_decisions` works on plain rows; `load_replay_rows`
only SELECTs. Nothing is stored, and a hard cap bounds the work.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, select

from app.analysis.ai_overlay_scoring import overlay_opposes_trade, score_ai_overlay
from app.config import AppSettings
from app.portfolio.calibration import MIN_TRADES_FOR_READING
from app.portfolio.missed_trade_models import OUTCOME_OPEN, OUTCOME_RESOLVED, MissedTradeOutcome
from app.portfolio.missed_trades import (
    CATEGORY_AI_VETO,
    CATEGORY_HELD_BY_AI,
    CATEGORY_LOW_CONFIDENCE,
    _direction_from_text,
    classify_plan,
)
from app.portfolio.models import PaperPosition, TradePlanRecord
from app.portfolio.signal_stats import bootstrap_mean_interval, wilson_interval
from app.services.trade_plan_service import MAX_SCORE_FOR_CONFIDENCE, _confidence_score, clamp_points

logger = logging.getLogger(__name__)

# The newest this many decisions are replayed; a longer history is cut and the
# result says so. Replaying is arithmetic on stored rows, so the cap only exists
# to keep one request bounded.
MAX_REPLAY_ROWS = 5000
# The longest list of flipped decisions returned (the counts above it cover all).
MAX_LISTED_FLIPS = 300

DIRECTION_BOTH = "both"
DIRECTIONS = ("both", "long", "short")

# The settings this replay can recompute, each with a plain description.
REPLAYABLE_KNOBS: dict[str, str] = {
    "min_confidence_for_trade": "Minimum confidence (%) a setup needs to be taken",
    "ai_overlay_objection_action": "What an AI objection does: cancel the trade, hold it for review, or nothing",
    "ai_overlay_scores_confidence": "Whether an AI objection costs confidence points",
    "allowed_directions": "Which directions may be traded: both, long only or short only",
}

_SIZING = (
    "changes how many shares are bought, which changes cash, the caps and every later trade; stored rows cannot "
    "replay that. Use a Backtest Lab run with this override instead."
)
_EXITS = (
    "changes when each trade ends, which needs the price path walked again; stored rows cannot replay that. "
    "Use a Backtest Lab run with this override instead."
)
UNREPLAYABLE_KNOBS: dict[str, str] = {
    "default_risk_pct": _SIZING,
    "paper_starting_cash": _SIZING,
    "max_concurrent_positions": _SIZING,
    "max_positions_per_sector": _SIZING,
    "max_position_pct_of_adv": _SIZING,
    "slippage_bps": _EXITS,
    "commission_per_trade": _EXITS,
    "max_holding_days": _EXITS,
}

REASON_NO_TREND = "No trend: there was no direction to trade, so no setting changes this decision."
REASON_ENGINE = "The plan was written but the engine did not fill it (cash, a cap, a stale price...): not a settings decision."
REASON_UNKNOWN_DIRECTION = "The direction of this decision is not recorded, so a direction or objection rule cannot be applied."
REASON_FLOORED = "The confidence points were floored at 0, which hides how many the rules earned before the AI penalty."
REASON_UNCLASSIFIED = "This decision does not match any known kind, so it is left as it was."

FLIP_NOW_TAKEN = "now_taken"
FLIP_NOW_SKIPPED = "now_skipped"

RESULT_RESOLVED = "resolved"
RESULT_OPEN = "open"
RESULT_NONE = "none"


class UnreplayableKnobError(ValueError):
    """The request names a setting the replay cannot recompute (or does not know)."""

    def __init__(self, rejected: dict[str, str]):
        self.rejected = rejected
        super().__init__("; ".join(f"{k}: {v}" for k, v in rejected.items()))


class ReplayOverrides(BaseModel):
    """The replayable settings; each None means "leave as the current setting"."""

    min_confidence_for_trade: int | None = Field(default=None, ge=0, le=100)
    ai_overlay_objection_action: Literal["cancel", "hold", "none"] | None = None
    ai_overlay_scores_confidence: bool | None = None
    allowed_directions: Literal["both", "long", "short"] | None = None

    model_config = {"extra": "forbid"}

    def given(self) -> dict[str, Any]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


def parse_overrides(raw: Mapping[str, Any]) -> ReplayOverrides:
    """Validate a request's overrides. Raises UnreplayableKnobError for any setting
    that cannot be replayed from stored rows (with the reason) or is unknown, and
    ValueError for a bad value or an empty request."""
    rejected: dict[str, str] = {}
    for key in raw:
        if key in REPLAYABLE_KNOBS:
            continue
        if key in UNREPLAYABLE_KNOBS:
            rejected[key] = UNREPLAYABLE_KNOBS[key]
        elif key in AppSettings.model_fields:
            rejected[key] = "is not a decision rule this replay can recompute from stored rows."
        else:
            rejected[key] = "is not a setting."
    if rejected:
        raise UnreplayableKnobError(rejected)
    try:
        parsed = ReplayOverrides(**raw)
    except ValidationError as exc:
        raise ValueError("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())) from exc
    if not parsed.given():
        raise ValueError("choose at least one setting to change: " + ", ".join(REPLAYABLE_KNOBS))
    return parsed


# ------------------------------------------------------------------ rows


@dataclass
class ReplayRow:
    """One stored decision: its decision inputs and what it earned or would have."""

    plan_id: int
    symbol: str
    created_at: datetime
    taken: bool  # a position was opened from it
    direction: str | None
    confidence_points: int | None
    ai_overlay_score: int  # what was actually applied to the points (0 or negative)
    ai_stance: str | None
    ai_verdict: str | None
    ai_conviction: int | None  # the model's stated conviction in its own stance
    strategy_version: int | None
    result_state: str = RESULT_NONE  # resolved | open | none
    result_r: float | None = None  # real R if taken, the hypothetical R if not
    category: str | None = None  # the missed-trades category, for a plan not taken
    # Why a setting cannot move this decision; None when it can.
    fixed_reason: str | None = None


@dataclass
class ReplayStats:
    taken: int
    resolved: int
    open: int
    no_result: int
    wins: int
    win_rate: float | None  # percent, of resolved
    win_rate_low: float | None  # Wilson 95%, percent
    win_rate_high: float | None
    avg_r: float | None
    avg_r_low: float | None  # bootstrap 95%
    avg_r_high: float | None
    total_r: float
    small_sample: bool


@dataclass
class FlippedDecision:
    plan_id: int
    symbol: str
    created_at: datetime
    direction: str | None
    flip: str  # now_taken | now_skipped
    why: str
    confidence_points: int | None
    points_after: int | None
    result_state: str
    result_r: float | None
    strategy_version: int | None


@dataclass
class VersionCount:
    strategy_version: int | None
    decisions: int


@dataclass
class ReplayResult:
    overrides: dict[str, Any]
    decisions: int
    truncated: bool
    before: ReplayStats
    after: ReplayStats
    flipped_count: int
    now_taken: int
    now_skipped: int
    flips_without_result: int
    flips: list[FlippedDecision]
    fixed_count: int  # decisions no replayable setting can move
    baseline_disagrees: int  # movable decisions where today's settings differ from what happened
    versions: list[VersionCount]
    replayable: dict[str, str]
    caveats: list[str] = field(default_factory=list)


CAVEATS: tuple[str, ...] = (
    "Only the settings listed as replayable can be recomputed from stored rows. Sizing, caps, slippage, commission, "
    "stops, targets and holding limits are refused: they need the Backtest Lab.",
    "A trade that would now be taken is scored with its hypothetical result from the missed-trades ledger: filled at the "
    "last known daily close and walked on daily bars only. It is an estimate, not what the account would have earned.",
    "Taking an extra trade changes cash and the position cap for the trades after it. This replay does not model that.",
    "A decision flips only when the new settings give a different verdict from today's settings on the same stored "
    "inputs. Where the rules were different when the decision was made, the 'before' column is still what really happened.",
    "A small number of flipped trades is an anecdote, not a finding. The ranges say how wide the doubt is.",
)


# ------------------------------------------------------------------ the decision


@dataclass(frozen=True)
class _Rules:
    min_confidence: int
    objection_action: str
    scores_confidence: bool
    allowed_directions: str


def _rules(settings: AppSettings, overrides: ReplayOverrides | None) -> _Rules:
    given = overrides.given() if overrides is not None else {}
    return _Rules(
        min_confidence=given.get("min_confidence_for_trade", settings.min_confidence_for_trade),
        objection_action=given.get("ai_overlay_objection_action", settings.ai_overlay_objection_action),
        scores_confidence=given.get("ai_overlay_scores_confidence", settings.ai_overlay_scores_confidence),
        allowed_directions=given.get("allowed_directions", DIRECTION_BOTH),
    )


def decide(row: ReplayRow, rules: _Rules) -> tuple[bool, int | None, str]:
    """(taken, confidence points, reason) for `row` under `rules`: the same order
    the live evaluation applies them (an objection first, then the confidence bar)."""
    direction = row.direction
    if rules.allowed_directions != DIRECTION_BOTH and direction != rules.allowed_directions:
        return False, row.confidence_points, f"{direction or 'unknown direction'} trades are filtered out"
    rule_points = (row.confidence_points or 0) - row.ai_overlay_score
    overlay = 0
    if rules.scores_confidence and direction is not None:
        overlay = score_ai_overlay(direction, row.ai_stance, row.ai_verdict, row.ai_conviction)[0]
    points = clamp_points(rule_points + overlay)
    if (
        rules.objection_action in ("cancel", "hold")
        and direction is not None
        and overlay_opposes_trade(direction, row.ai_stance, row.ai_verdict)
    ):
        verb = "cancels" if rules.objection_action == "cancel" else "holds"
        return False, points, f"the AI objection {verb} the trade"
    confidence = _confidence_score(points)
    if confidence < rules.min_confidence:
        return False, points, f"confidence {confidence}% is under the {rules.min_confidence}% bar"
    return True, points, f"confidence {confidence}% clears the {rules.min_confidence}% bar"


# ------------------------------------------------------------------ the stats


def _stats(rows: list[tuple[ReplayRow, bool]]) -> ReplayStats:
    """Stats of the decisions that are taken in the second tuple slot."""
    taken = [r for r, is_taken in rows if is_taken]
    resolved = [r.result_r for r in taken if r.result_state == RESULT_RESOLVED and r.result_r is not None]
    wins = sum(1 for r in resolved if r > 0)
    wilson = wilson_interval(wins, len(resolved))
    interval = bootstrap_mean_interval(resolved) if len(resolved) >= 2 else None
    return ReplayStats(
        taken=len(taken),
        resolved=len(resolved),
        open=sum(1 for r in taken if r.result_state == RESULT_OPEN),
        no_result=sum(1 for r in taken if r.result_state == RESULT_NONE),
        wins=wins,
        win_rate=wins / len(resolved) * 100 if resolved else None,
        win_rate_low=wilson[0] * 100 if wilson else None,
        win_rate_high=wilson[1] * 100 if wilson else None,
        avg_r=sum(resolved) / len(resolved) if resolved else None,
        avg_r_low=interval[0] if interval else None,
        avg_r_high=interval[1] if interval else None,
        total_r=sum(resolved),
        small_sample=len(resolved) < MIN_TRADES_FOR_READING,
    )


def replay_decisions(
    rows: list[ReplayRow],
    settings: AppSettings,
    overrides: ReplayOverrides,
    *,
    truncated: bool = False,
) -> ReplayResult:
    """Pure: re-decide every row under `overrides` (on top of `settings`) and compare."""
    today_rules = _rules(settings, None)
    new_rules = _rules(settings, overrides)

    before: list[tuple[ReplayRow, bool]] = []
    after: list[tuple[ReplayRow, bool]] = []
    flips: list[FlippedDecision] = []
    fixed = 0
    disagrees = 0
    versions: dict[int | None, int] = {}
    for row in rows:
        versions[row.strategy_version] = versions.get(row.strategy_version, 0) + 1
        before.append((row, row.taken))
        if row.fixed_reason is not None:
            fixed += 1
            after.append((row, row.taken))
            continue
        today_take, _, _ = decide(row, today_rules)
        new_take, new_points, why = decide(row, new_rules)
        if today_take != row.taken:
            disagrees += 1
        # Only a change of verdict relative to today's settings moves a decision; if that
        # lands on what really happened (taken in fact, taken again) nothing moved.
        after_take = new_take if new_take != today_take else row.taken
        after.append((row, after_take))
        if after_take == row.taken:
            continue
        flips.append(
            FlippedDecision(
                plan_id=row.plan_id,
                symbol=row.symbol,
                created_at=row.created_at,
                direction=row.direction,
                flip=FLIP_NOW_TAKEN if after_take else FLIP_NOW_SKIPPED,
                why=why[0].upper() + why[1:],
                confidence_points=row.confidence_points,
                points_after=new_points,
                result_state=row.result_state,
                result_r=row.result_r,
                strategy_version=row.strategy_version,
            )
        )
    flips.sort(key=lambda f: f.created_at, reverse=True)
    return ReplayResult(
        overrides=overrides.given(),
        decisions=len(rows),
        truncated=truncated,
        before=_stats(before),
        after=_stats(after),
        flipped_count=len(flips),
        now_taken=sum(1 for f in flips if f.flip == FLIP_NOW_TAKEN),
        now_skipped=sum(1 for f in flips if f.flip == FLIP_NOW_SKIPPED),
        flips_without_result=sum(1 for f in flips if f.result_state != RESULT_RESOLVED),
        flips=flips[:MAX_LISTED_FLIPS],
        fixed_count=fixed,
        baseline_disagrees=disagrees,
        versions=[VersionCount(v, n) for v, n in sorted(versions.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))],
        replayable=dict(REPLAYABLE_KNOBS),
        caveats=list(CAVEATS),
    )


# ------------------------------------------------------------------ loading


def load_replay_rows(session: Session, limit: int = MAX_REPLAY_ROWS) -> tuple[list[ReplayRow], bool]:
    """The stored decisions as replay rows, newest first, and whether the history was cut.
    Only SELECTs: nothing is written."""
    plans = list(
        session.exec(
            select(TradePlanRecord).where(TradePlanRecord.status != "discarded").order_by(TradePlanRecord.created_at.desc()).limit(limit + 1)
        ).all()
    )
    truncated = len(plans) > limit
    plans = plans[:limit]
    ids = [p.id for p in plans if p.id is not None]
    positions: dict[int, PaperPosition] = {}
    outcomes: dict[int, MissedTradeOutcome] = {}
    if ids:
        for position in session.exec(select(PaperPosition).where(PaperPosition.trade_plan_id.in_(ids))).all():
            positions[position.trade_plan_id] = position
        for outcome in session.exec(select(MissedTradeOutcome).where(MissedTradeOutcome.plan_id.in_(ids))).all():
            outcomes[outcome.plan_id] = outcome
    return [_to_row(plan, positions.get(plan.id), outcomes.get(plan.id)) for plan in plans], truncated


def points_from_score(confidence_score: int) -> int:
    """The evidence points behind a stored confidence percentage. The percentage is
    points / MAX_SCORE_FOR_CONFIDENCE rounded, and only 17 values are reachable, so
    the mapping back is exact (the plan row stores the percentage, not the points)."""
    return clamp_points(round(confidence_score * MAX_SCORE_FOR_CONFIDENCE / 100))


def _to_row(plan: TradePlanRecord, position: PaperPosition | None, outcome: MissedTradeOutcome | None) -> ReplayRow:
    taken = position is not None
    direction = plan.direction or (position.direction if position else None) or (outcome.direction if outcome else None)
    if direction is None and not taken:
        direction = _direction_from_text(plan)
    row = ReplayRow(
        plan_id=plan.id,
        symbol=plan.symbol,
        created_at=plan.created_at,
        taken=taken,
        direction=direction,
        confidence_points=points_from_score(plan.confidence_score),
        ai_overlay_score=min(0, plan.ai_overlay_score or 0),
        ai_stance=plan.ai_opinion_stance,
        ai_verdict=plan.ai_trade_verdict,
        ai_conviction=plan.ai_opinion_score,
        strategy_version=plan.strategy_version,
    )
    if taken:
        if position.status == "closed" and position.realized_r is not None:
            row.result_state, row.result_r = RESULT_RESOLVED, position.realized_r
        elif position.status == "open":
            row.result_state = RESULT_OPEN
    else:
        classification = classify_plan(plan)
        row.category = classification.category if classification else None
        if outcome is not None and outcome.status == OUTCOME_RESOLVED and outcome.r_multiple is not None:
            row.result_state, row.result_r = RESULT_RESOLVED, outcome.r_multiple
        elif outcome is not None and outcome.status == OUTCOME_OPEN:
            row.result_state = RESULT_OPEN
    row.fixed_reason = _fixed_reason(plan, row)
    return row


def _fixed_reason(plan: TradePlanRecord, row: ReplayRow) -> str | None:
    """Why no replayable setting can move this decision, or None when one can."""
    if row.ai_overlay_score < 0 and row.confidence_points == 0:
        return REASON_FLOORED
    if row.taken:
        return None
    if row.category in (CATEGORY_LOW_CONFIDENCE, CATEGORY_AI_VETO, CATEGORY_HELD_BY_AI):
        return None if row.direction is not None else REASON_UNKNOWN_DIRECTION
    if (plan.reason or "").startswith("No clear trend"):
        return REASON_NO_TREND
    if row.category is None:
        return REASON_UNCLASSIFIED
    return REASON_ENGINE


def replay_from_session(session: Session, settings: AppSettings, overrides: ReplayOverrides) -> ReplayResult:
    rows, truncated = load_replay_rows(session)
    return replay_decisions(rows, settings, overrides, truncated=truncated)

