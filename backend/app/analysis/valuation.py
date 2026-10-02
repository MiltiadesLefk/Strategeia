"""Valuation maths: a simple discounted-earnings projection and peer multiples.

Pure functions, no I/O. Valuation is informational: nothing here feeds a trade
plan's score, direction or size. The projection discounts net income (the free
sources give no cash-flow statement), so it is a rough equity-value estimate,
and every input is shown and editable rather than hidden.

The layout (assumptions, projection years, terminal value, sensitivity grid,
peer median multiples) follows the usual DCF and comps structure described in
the Anthropic financial-services skills (Apache-2.0); no text or code was copied.
"""

from __future__ import annotations

from statistics import median

from app.schemas.valuation_schemas import (
    DcfAssumptions,
    DcfResult,
    DcfYear,
    MultipleSummary,
    PeerRow,
    SensitivityCell,
)

# A growth rate taken from history is clamped: one boom year must not become
# five years of 60% growth. The user can still type anything inside the limits.
DEFAULT_GROWTH_FLOOR_PCT = -10.0
DEFAULT_GROWTH_CAP_PCT = 25.0
INPUT_GROWTH_RANGE = (-50.0, 100.0)
INPUT_MARGIN_RANGE = (-100.0, 100.0)
# The discount rate must stay well above terminal growth or the terminal value
# explodes (the formula divides by their gap).
MIN_RATE_GAP_PCT = 1.0
DEFAULT_DISCOUNT_PCT = 10.0
DEFAULT_TERMINAL_PCT = 2.5
DEFAULT_YEARS = 5
MAX_YEARS = 10
# Sensitivity grid steps (percentage points) around the chosen rates.
SENSITIVITY_DISCOUNT_STEP = 1.0
SENSITIVITY_TERMINAL_STEP = 0.5


def default_growth_pct(revenues: list[float]) -> tuple[float | None, str]:
    """Compound annual revenue growth over the reported years (oldest first)."""
    if len(revenues) < 2 or revenues[0] <= 0 or revenues[-1] <= 0:
        return None, "not available (needs two reported years with revenue)"
    periods = len(revenues) - 1
    cagr = ((revenues[-1] / revenues[0]) ** (1 / periods) - 1) * 100
    clamped = max(DEFAULT_GROWTH_FLOOR_PCT, min(DEFAULT_GROWTH_CAP_PCT, cagr))
    note = f"revenue growth over {periods} reported year(s)"
    if clamped != cagr:
        note += f", limited from {cagr:.1f}%"
    return clamped, note


def _earnings_path(base_revenue: float, growth_pct: float, margin_pct: float, years: int) -> list[tuple[float, float]]:
    out = []
    revenue = base_revenue
    for _ in range(years):
        revenue *= 1 + growth_pct / 100
        out.append((revenue, revenue * margin_pct / 100))
    return out


def _equity_value(base_revenue: float, growth: float, margin: float, discount: float, terminal: float, years: int) -> float:
    path = _earnings_path(base_revenue, growth, margin, years)
    pv = sum(e / (1 + discount / 100) ** (i + 1) for i, (_, e) in enumerate(path))
    tv = path[-1][1] * (1 + terminal / 100) / ((discount - terminal) / 100)
    return pv + tv / (1 + discount / 100) ** years


def validate_inputs(growth: float, margin: float, discount: float, terminal: float, years: int) -> str | None:
    if not INPUT_GROWTH_RANGE[0] <= growth <= INPUT_GROWTH_RANGE[1]:
        return f"Growth must be between {INPUT_GROWTH_RANGE[0]:.0f}% and {INPUT_GROWTH_RANGE[1]:.0f}%."
    if not INPUT_MARGIN_RANGE[0] <= margin <= INPUT_MARGIN_RANGE[1]:
        return "Net margin must be between -100% and 100%."
    if not 1 <= years <= MAX_YEARS:
        return f"Years must be between 1 and {MAX_YEARS}."
    if discount - terminal < MIN_RATE_GAP_PCT:
        return f"The discount rate must be at least {MIN_RATE_GAP_PCT:.0f} point above terminal growth."
    return None


def run_dcf(base_revenue: float, assumptions: DcfAssumptions, shares: float | None, price: float | None) -> DcfResult:
    a = assumptions
    path = _earnings_path(base_revenue, a.growth_pct, a.net_margin_pct, a.years)
    rows = [
        DcfYear(year=i + 1, revenue=rev, earnings=earn, present_value=earn / (1 + a.discount_rate_pct / 100) ** (i + 1))
        for i, (rev, earn) in enumerate(path)
    ]
    tv = path[-1][1] * (1 + a.terminal_growth_pct / 100) / ((a.discount_rate_pct - a.terminal_growth_pct) / 100)
    tv_pv = tv / (1 + a.discount_rate_pct / 100) ** a.years
    equity = sum(r.present_value for r in rows) + tv_pv
    per_share = equity / shares if shares and shares > 0 else None
    upside = (per_share / price - 1) * 100 if per_share is not None and price and price > 0 else None
    tv_share = tv_pv / equity * 100 if equity > 0 else 0.0

    grid: list[SensitivityCell] = []
    for dd in (-SENSITIVITY_DISCOUNT_STEP, 0.0, SENSITIVITY_DISCOUNT_STEP):
        for dt in (-SENSITIVITY_TERMINAL_STEP, 0.0, SENSITIVITY_TERMINAL_STEP):
            d, t = a.discount_rate_pct + dd, a.terminal_growth_pct + dt
            value = None
            if d - t >= MIN_RATE_GAP_PCT and shares and shares > 0:
                value = _equity_value(base_revenue, a.growth_pct, a.net_margin_pct, d, t, a.years) / shares
            grid.append(SensitivityCell(discount_rate_pct=d, terminal_growth_pct=t, value_per_share=value))
    return DcfResult(
        assumptions=a,
        projection=rows,
        terminal_value=tv,
        terminal_present_value=tv_pv,
        equity_value=equity,
        shares=shares,
        value_per_share=per_share,
        price=price,
        upside_pct=upside,
        terminal_share_pct=tv_share,
        sensitivity=grid,
    )


def summarise_multiple(name: str, subject: float | None, peer_values: list[float | None], subject_per_unit: float | None) -> MultipleSummary:
    """Median/low/high of the usable peer values. A zero or negative multiple
    (a loss-making peer) says nothing about price, so it is left out.
    `subject_per_unit` is the subject's own earnings (or sales) per share."""
    usable = [v for v in peer_values if v is not None and v > 0]
    if not usable:
        return MultipleSummary(name=name, subject=subject, count=0)
    med = float(median(usable))
    implied = med * subject_per_unit if subject_per_unit is not None and subject_per_unit > 0 else None
    return MultipleSummary(name=name, subject=subject, median=med, low=min(usable), high=max(usable), count=len(usable), implied_price=implied)


def peer_summaries(peers: list[PeerRow], subject_pe: float | None, subject_ps: float | None, eps: float | None, sales_per_share: float | None) -> list[MultipleSummary]:
    return [
        summarise_multiple("P/E", subject_pe, [p.pe_ratio for p in peers], eps),
        summarise_multiple("P/S", subject_ps, [p.price_to_sales for p in peers], sales_per_share),
    ]
