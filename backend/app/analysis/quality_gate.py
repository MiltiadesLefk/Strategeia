"""The "good stock" gate (PORTOFOLIO_BUILDER.md step 4): pass/fail with a reason for every check.

Plain rules, no AI. A check whose data is missing FAILS with that said (never a guess), so a stock
is only bought when every part was actually read. Thresholds are starting values to be tuned with a
backtest, not by feel (open decision O2).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.valuation_scoring import score_valuation
from app.data_providers.base import CompanyOverview, FinancialYear

# Close above the 200-day average (needs this many daily bars).
MA_DAYS = 200
# Latest year's revenue must not be below the prior year's by more than this.
MIN_REVENUE_GROWTH_PCT = 0.0


@dataclass
class GateCheck:
    name: str
    ok: bool
    note: str


@dataclass
class GateResult:
    passed: bool
    checks: list[GateCheck]

    @property
    def failed_reasons(self) -> list[str]:
        return [f"{c.name}: {c.note}" for c in self.checks if not c.ok]


def evaluate_quality_gate(
    price: float,
    closes: list[float],
    overview: CompanyOverview | None,
    financial_years: list[FinancialYear],
) -> GateResult:
    checks: list[GateCheck] = []

    if len(closes) >= MA_DAYS:
        ma = sum(closes[-MA_DAYS:]) / MA_DAYS
        checks.append(GateCheck("trend", price > ma, f"price {price:,.2f} vs 200-day average {ma:,.2f}"))
    else:
        checks.append(GateCheck("trend", False, f"only {len(closes)} daily bars, {MA_DAYS} needed"))

    if len(financial_years) >= 2 and financial_years[-2].revenue:
        latest, prior = financial_years[-1], financial_years[-2]
        growth = (latest.revenue - prior.revenue) / prior.revenue * 100
        checks.append(GateCheck("growth", growth >= MIN_REVENUE_GROWTH_PCT, f"revenue {growth:+.1f}% year on year"))
        margin = latest.net_income / latest.revenue * 100 if latest.revenue else None
        checks.append(
            GateCheck("profit", latest.net_income > 0, f"net income ${latest.net_income / 1e6:,.0f}M"
                      + (f" (margin {margin:.1f}%)" if margin is not None else ""))
        )
    else:
        note = f"{len(financial_years)} reported year(s) of financials, two needed"
        checks += [GateCheck("growth", False, note), GateCheck("profit", False, note)]

    if overview is None:
        checks.append(GateCheck("valuation", False, "no company overview"))
    else:
        points, reasons = score_valuation("long", overview, financial_years)
        checks.append(GateCheck("valuation", points >= 0, "; ".join(reasons) or "not expensive on earnings"))

    return GateResult(all(c.ok for c in checks), checks)
