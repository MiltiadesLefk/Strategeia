from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from app.analysis.indicators import Level

Direction = Literal["long", "short"]

FALLBACK_R_MULTIPLES = (1.5, 3.0)
MIN_RR_FOR_LEVEL_TARGET = 1.0


@dataclass
class PositionSizeResult:
    shares: int
    risk_per_share: float
    account_risk_dollars: float
    position_value: float
    capped_by_cash: bool = False


def calculate_position_size(
    account_size: float,
    risk_pct: float,
    entry: float,
    stop: float,
    available_cash: float | None = None,
) -> PositionSizeResult:
    risk_per_share = abs(entry - stop)
    account_risk_dollars = account_size * (risk_pct / 100)
    if risk_per_share <= 0:
        return PositionSizeResult(0, 0.0, account_risk_dollars, 0.0)

    shares = math.floor(account_risk_dollars / risk_per_share)
    capped = False
    if available_cash is not None and shares * entry > available_cash:
        shares = math.floor(available_cash / entry) if entry > 0 else 0
        capped = True

    return PositionSizeResult(
        shares=shares,
        risk_per_share=risk_per_share,
        account_risk_dollars=account_risk_dollars,
        position_value=shares * entry,
        capped_by_cash=capped,
    )


@dataclass
class TargetsResult:
    tp1: float
    tp2: float
    rr1: float
    rr2: float


def _reward_to_risk(entry: float, target: float, risk_per_share: float, direction: Direction) -> float:
    diff = (target - entry) if direction == "long" else (entry - target)
    return diff / risk_per_share if risk_per_share > 0 else 0.0


def derive_targets(
    entry: float,
    stop: float,
    direction: Direction,
    support_levels: list[Level] | list[float],
    resistance_levels: list[Level] | list[float],
    fallback_r: tuple[float, float] = FALLBACK_R_MULTIPLES,
    min_rr: float = MIN_RR_FOR_LEVEL_TARGET,
) -> TargetsResult:
    risk_per_share = abs(entry - stop)
    sign = 1 if direction == "long" else -1

    def price_of(level: Level | float) -> float:
        return level.price if isinstance(level, Level) else level

    if direction == "long":
        candidates = sorted(
            (price_of(lvl) for lvl in resistance_levels if price_of(lvl) > entry),
            key=lambda p: p - entry,
        )
    else:
        candidates = sorted(
            (price_of(lvl) for lvl in support_levels if price_of(lvl) < entry),
            key=lambda p: entry - p,
        )

    def pick(index: int, fallback_multiple: float) -> float:
        if index < len(candidates):
            candidate = candidates[index]
            if _reward_to_risk(entry, candidate, risk_per_share, direction) >= min_rr:
                return candidate
        return entry + sign * fallback_multiple * risk_per_share

    tp1 = pick(0, fallback_r[0])
    tp2 = pick(1, fallback_r[1])
    if tp2 == tp1:
        tp2 = entry + sign * fallback_r[1] * risk_per_share

    return TargetsResult(
        tp1=tp1,
        tp2=tp2,
        rr1=_reward_to_risk(entry, tp1, risk_per_share, direction),
        rr2=_reward_to_risk(entry, tp2, risk_per_share, direction),
    )
