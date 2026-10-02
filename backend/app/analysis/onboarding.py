"""First-run questionnaire: three answers in, a suggested trading style and a
risk-per-trade percentage out.

Plain rules, no AI, no I/O. The result is only a suggestion: nothing here
reads or writes settings, and the dashboard changes a setting only when the
user presses Apply.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

RiskTolerance = Literal["low", "medium", "high"]
TimeHorizon = Literal["days", "weeks", "months"]
Experience = Literal["beginner", "intermediate", "advanced"]

DEFAULT_RISK_PCT = 1.0
MIN_SUGGESTED_RISK_PCT = 0.25
MAX_SUGGESTED_RISK_PCT = 2.0

# Share of the account risked between entry and stop, by comfort with losses.
RISK_BY_TOLERANCE: dict[str, float] = {"low": 0.5, "medium": DEFAULT_RISK_PCT, "high": 1.5}
# A beginner is steered lower; an advanced user may go a step higher, but only
# when they also say they are comfortable with losses.
BEGINNER_REDUCTION = 0.5
ADVANCED_BONUS = 0.5

STYLE_BY_HORIZON: dict[str, tuple[str, str]] = {
    "days": ("short swing", "Trades held for a few days, using quick moves off support or resistance."),
    "weeks": ("swing", "Trades held for one to four weeks, the horizon this app's plans are written for."),
    "months": ("position", "Trades held for months, tolerating larger pullbacks in exchange for fewer trades."),
}


class OnboardingAnswers(BaseModel):
    risk_tolerance: RiskTolerance
    time_horizon: TimeHorizon
    experience: Experience


class OnboardingSuggestion(BaseModel):
    style: str
    style_description: str
    risk_pct: float
    reasons: list[str]


def suggest(answers: OnboardingAnswers) -> OnboardingSuggestion:
    style, style_description = STYLE_BY_HORIZON[answers.time_horizon]
    risk = RISK_BY_TOLERANCE[answers.risk_tolerance]
    reasons = [f"You are {answers.risk_tolerance} on risk, which starts the risk per trade at {risk:g}%."]
    if answers.experience == "beginner":
        risk -= BEGINNER_REDUCTION
        reasons.append(f"As a beginner it is lowered by {BEGINNER_REDUCTION:g} points while you learn how losing streaks feel.")
    elif answers.experience == "advanced" and answers.risk_tolerance == "high":
        risk += ADVANCED_BONUS
        reasons.append(f"Experience and a high tolerance add {ADVANCED_BONUS:g} points.")
    risk = round(min(max(risk, MIN_SUGGESTED_RISK_PCT), MAX_SUGGESTED_RISK_PCT), 2)
    reasons.append(f"A {answers.time_horizon} horizon points to the {style} style.")
    return OnboardingSuggestion(style=style, style_description=style_description, risk_pct=risk, reasons=reasons)
