"""The "good stock" gate (PORTOFOLIO_BUILDER.md step 4): pass/fail with a reason for every check.

Plain rules, no AI. A check whose data is missing FAILS with that said (never a guess), so a stock
is only bought when every part was actually read. Thresholds are starting values to be tuned with a
backtest, not by feel (open decision O2).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.price_evidence import ACCUMULATION_RATIO
from app.analysis.valuation_scoring import score_valuation
from app.data_providers.base import CompanyOverview, FinancialYear

# Close above the 200-day average (needs this many daily bars).
MA_DAYS = 200
MA_SLOPE_LOOKBACK = 20  # require_rising_ma200: the 200-day average must be >= its value this many bars ago


# require_financial_health: the latest reported year must show low debt, enough liquidity and positive free cash flow.
MAX_DEBT_TO_EQUITY = 1.0
MIN_CURRENT_RATIO = 1.0


# The dip rule (PORTOFOLIO_BUILDER O3): a good stock at least this % below its high of the last
# DIP_LOOKBACK trading days (about 3 months). Price only, no AI.
DIP_PCT = 10.0
DIP_LOOKBACK = 63


def is_dip(closes: list[float]) -> bool:
    """The latest close is DIP_PCT or more under the highest close of the last DIP_LOOKBACK days."""
    window = closes[-DIP_LOOKBACK:]
    return len(window) >= DIP_LOOKBACK and window[-1] <= max(window) * (1 - DIP_PCT / 100)


@dataclass
class GateCheck:
    name: str
    ok: bool
    note: str


@dataclass
class GateResult:
    passed: bool
    checks: list[GateCheck]
    dip: bool = False  # price-only: see is_dip
    detail: dict | None = None  # the evidence rating's per-part points and reasons (services/rating_service)

    @property
    def failed_reasons(self) -> list[str]:
        return [f"{c.name}: {c.note}" for c in self.checks if not c.ok]


def _operating_leverage_check(latest: FinancialYear, prior: FinancialYear, revenue_growth: float) -> GateCheck:
    """Operating income grew faster than revenue, and revenue grew. Unreported figures fail, never a guess."""
    o1, o0 = latest.operating_income, prior.operating_income
    if o1 is None or o0 is None:
        return GateCheck("operating_leverage", False, "operating income not reported for both years")
    if revenue_growth <= 0:
        return GateCheck("operating_leverage", False, f"revenue {revenue_growth:+.1f}% (must be growing)")
    if o0 <= 0:  # a percentage off a zero or negative base means nothing: only a swing to a profit counts
        return GateCheck("operating_leverage", o1 > 0, f"operating income ${o0 / 1e6:,.0f}M -> ${o1 / 1e6:,.0f}M (no % off a base of zero or less; passes only by turning positive)")
    og = (o1 - o0) / o0 * 100
    return GateCheck("operating_leverage", og > revenue_growth, f"operating income {og:+.1f}% vs revenue {revenue_growth:+.1f}%")


def _financial_health_check(latest: FinancialYear) -> GateCheck:
    """Debt-to-equity, current ratio and free cash flow all read and all inside their bars. Unreported figures fail."""
    de, cr, fcf = latest.debt_to_equity, latest.current_ratio, latest.free_cash_flow
    missing = [n for n, v in (("debt-to-equity", de), ("current ratio", cr), ("free cash flow", fcf)) if v is None]
    if missing:
        return GateCheck("financial_health", False, f"{', '.join(missing)} not reported")
    ok = de <= MAX_DEBT_TO_EQUITY and cr >= MIN_CURRENT_RATIO and fcf > 0
    return GateCheck("financial_health", ok, f"debt-to-equity {de:.2f} (max {MAX_DEBT_TO_EQUITY:g}), current ratio {cr:.2f} (min {MIN_CURRENT_RATIO:g}), free cash flow ${fcf / 1e6:,.0f}M")


def evaluate_quality_gate(
    price: float,
    closes: list[float],
    overview: CompanyOverview | None,
    financial_years: list[FinancialYear],
    require_trend: bool = True,  # False = the rebuy gate: a stock that just fell is rarely above its 200-day
    min_growth_pct: float = 5.0,  # AppSettings.hold_min_revenue_growth_pct
    require_acceleration: bool = False,  # AppSettings.require_growth_acceleration
    price_only: bool = False,  # trend check only (+ the dip flag): what a backtest can know at T. Never a pass by default
    volume_ratio: float | None = None,  # up_down_volume_ratio of the recent bars (None = unreadable)
    block_on_distribution: bool = False,  # AppSettings.block_on_distribution
    require_accumulation: bool = False,  # AppSettings.require_accumulation (stricter)
    require_rising_ma200: bool = False,  # AppSettings.require_rising_ma200
    max_extension_above_ma200_pct: float = 0.0,  # AppSettings.max_extension_above_ma200_pct (0 = off)
    require_operating_leverage: bool = False,  # AppSettings.require_operating_leverage
    require_financial_health: bool = False,  # AppSettings.require_financial_health
) -> GateResult:
    checks: list[GateCheck] = []

    if not require_trend and not price_only:
        pass
    elif len(closes) >= MA_DAYS:
        ma = sum(closes[-MA_DAYS:]) / MA_DAYS
        checks.append(GateCheck("trend", price > ma, f"price {price:,.2f} vs 200-day average {ma:,.2f}"))
        if max_extension_above_ma200_pct > 0:
            ext = (price / ma - 1) * 100
            checks.append(GateCheck("extension", ext <= max_extension_above_ma200_pct, f"price {ext:+.1f}% vs 200-day average (limit +{max_extension_above_ma200_pct:g}%)"))
    else:
        checks.append(GateCheck("trend", False, f"only {len(closes)} daily bars, {MA_DAYS} needed"))

    if require_rising_ma200 and (require_trend or price_only):
        if len(closes) >= MA_DAYS + MA_SLOPE_LOOKBACK:
            now = sum(closes[-MA_DAYS:]) / MA_DAYS
            before = sum(closes[-MA_DAYS - MA_SLOPE_LOOKBACK : -MA_SLOPE_LOOKBACK]) / MA_DAYS
            checks.append(GateCheck("ma_slope", now >= before, f"200-day average {now:,.2f} vs {before:,.2f} {MA_SLOPE_LOOKBACK} bars ago"))
        else:
            checks.append(GateCheck("ma_slope", False, f"only {len(closes)} daily bars, {MA_DAYS + MA_SLOPE_LOOKBACK} needed for the 200-day slope"))

    if price_only:  # growth, profit and valuation are not run at all (not passed, not failed)
        return GateResult(all(c.ok for c in checks), checks, dip=is_dip(closes))

    if len(financial_years) >= 2 and financial_years[-2].revenue:
        latest, prior = financial_years[-1], financial_years[-2]
        growth = (latest.revenue - prior.revenue) / prior.revenue * 100
        checks.append(GateCheck("growth", growth >= min_growth_pct, f"revenue {growth:+.1f}% year on year (bar {min_growth_pct:g}%)"))
        if require_acceleration:
            if len(financial_years) >= 3 and financial_years[-3].revenue:
                before = (prior.revenue - financial_years[-3].revenue) / financial_years[-3].revenue * 100
                checks.append(GateCheck("acceleration", growth > before, f"growth {growth:+.1f}% vs {before:+.1f}% the year before"))
            else:
                checks.append(GateCheck("acceleration", False, f"{len(financial_years)} reported year(s) of financials, three needed"))
        if require_operating_leverage:
            checks.append(_operating_leverage_check(latest, prior, growth))
        if require_financial_health:
            checks.append(_financial_health_check(latest))
        margin = latest.net_income / latest.revenue * 100 if latest.revenue else None
        checks.append(
            GateCheck("profit", latest.net_income > 0, f"net income ${latest.net_income / 1e6:,.0f}M"
                      + (f" (margin {margin:.1f}%)" if margin is not None else ""))
        )
    else:
        note = f"{len(financial_years)} reported year(s) of financials, two needed"
        checks += [GateCheck("growth", False, note), GateCheck("profit", False, note)]
        if require_acceleration:
            checks.append(GateCheck("acceleration", False, note))
        if require_operating_leverage:
            checks.append(GateCheck("operating_leverage", False, note))
        if require_financial_health:
            checks.append(GateCheck("financial_health", False, note))

    if overview is None:
        checks.append(GateCheck("valuation", False, "no company overview"))
    else:
        points, reasons = score_valuation("long", overview, financial_years)
        checks.append(GateCheck("valuation", points >= 0, "; ".join(reasons) or "not expensive on earnings"))

    if block_on_distribution or require_accumulation:
        if volume_ratio is None:
            checks.append(GateCheck("volume", False, "up/down volume ratio unreadable"))
        elif require_accumulation:
            checks.append(GateCheck("volume", volume_ratio >= ACCUMULATION_RATIO, f"up-day volume {volume_ratio:.2f}x down-day volume (accumulation needs {ACCUMULATION_RATIO:g}x)"))
        else:
            checks.append(GateCheck("volume", volume_ratio > 1 / ACCUMULATION_RATIO, f"up-day volume {volume_ratio:.2f}x down-day volume (distribution at {1 / ACCUMULATION_RATIO:.2f}x or less)"))

    return GateResult(all(c.ok for c in checks), checks, dip=is_dip(closes))
