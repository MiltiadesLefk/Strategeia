"""Profitability trend as a scored confluence check.

Reads the reported years (revenue and net income, oldest first) and asks one question: is the business
profitable and improving, or losing money and sliding? Rules only, no AI.

  * Healthy: the latest year is profitable with a net margin of at least HEALTHY_NET_MARGIN_PCT and net
    income did not fall against the year before.
  * Weak: the latest year is a net loss, or net income fell and the margin is under WEAK_NET_MARGIN_PCT.
  * Anything else is mixed and scores 0.

Direction-signed like the app's other confluence checks: healthy supports a long (+1) and argues against a
short (-1); weak does the opposite. With no direction, or fewer than two reported years, it scores 0. The
free sources give only revenue and net income by year (no balance sheet or cash flow), so this is a
profitability read, not a full financial-health audit, and it says so.
"""

from __future__ import annotations

from app.data_providers.base import FinancialYear

FINANCIAL_HEALTH_SCORE_CAP = 1
HEALTHY_NET_MARGIN_PCT = 10.0
WEAK_NET_MARGIN_PCT = 5.0


def score_financial_health(direction: str | None, financial_years: list[FinancialYear]) -> tuple[int, list[str]]:
    if direction not in ("long", "short") or len(financial_years) < 2:
        return 0, []
    latest, prior = financial_years[-1], financial_years[-2]
    if not latest.revenue:
        return 0, []
    margin = latest.net_income / latest.revenue * 100
    fell = latest.net_income < prior.net_income
    if latest.net_income > 0 and margin >= HEALTHY_NET_MARGIN_PCT and not fell:
        healthy, text = True, f"profitable and improving (net margin {margin:.1f}%, net income not down on the year before)"
    elif latest.net_income < 0 or (fell and margin < WEAK_NET_MARGIN_PCT):
        healthy = False
        text = "loss-making" if latest.net_income < 0 else f"profits falling on a thin net margin ({margin:.1f}%)"
        text = f"{text} in {latest.year}"
    else:
        return 0, []
    supports = healthy == (direction == "long")
    verb = "supports" if supports else "argues against"
    points = FINANCIAL_HEALTH_SCORE_CAP if supports else -FINANCIAL_HEALTH_SCORE_CAP
    return points, [f"financials: {text}, which {verb} a {direction}"]
