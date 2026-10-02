"""The model's inputs: only what the rules engine had computed at the moment of the
decision. Pure functions; no heavy imports."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# The score components a plan carries. The AI overlay's is left out: backtests run
# without an LLM, so it is always 0 in training and would only be noise.
FEATURE_COMPONENTS = (
    "technical_score",
    "fundamental_score",
    "news_score",
    "market_confirmation_score",
    "vix_regime_score",
    "options_score",
    "insider_score",
    "expected_move_score",
    "earnings_surprise_score",
    "macro_event_score",
)
FEATURE_NAMES: tuple[str, ...] = (*FEATURE_COMPONENTS, "confidence_points", "is_long")


def _num(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def feature_vector(scores: Mapping[str, Any] | None, confidence_points: Any, direction: str | None) -> list[float]:
    """One row in FEATURE_NAMES order. A missing component is 0 points, which is
    what the engine means by it."""
    scores = scores or {}
    row = [_num(scores.get(name)) for name in FEATURE_COMPONENTS]
    row.append(_num(confidence_points))
    row.append(1.0 if direction == "long" else 0.0)
    return row
