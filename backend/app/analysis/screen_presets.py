# From Anthropic's financial-services skills (anthropics/financial-services).
# Licensed under the Apache License, Version 2.0; full text in THIRD_PARTY_NOTICES.md.
# Adapted from anthropics/financial-services@574ed36
# plugins/vertical-plugins/equity-research/skills/idea-generation/SKILL.md; changes: the skill's
# five screens (value, growth, quality, short ideas, special situations) and their criteria
# lists became the declarative presets below. Only the criteria our own data can answer are
# implemented, each as a plain rule with a named threshold; the rest (EV/EBITDA, free cash
# flow yield, ROE, short interest, spin-offs, ...) are listed per preset as unavailable with
# the reason, never approximated. "Below the sector median" became fixed thresholds because we
# hold no peer medians.
"""Screen presets for the Market Scan page: named, declarative rule filters over data we hold.

A preset is a list of criteria. Each criterion reads one symbol's data and answers True, False, or
None ("cannot tell": the data it needs is missing). A symbol MATCHES a preset when every criterion
marked `required` is True and at least `min_matches` criteria in all are True. A required criterion
that comes back None makes the symbol "unjudged" rather than a match or a miss: missing data is
never read as passing, and never as failing either.

These are screens, not signals. They surface names worth a closer look, in the same spirit as the
scanner itself; they do not feed any score, plan or trade decision. Every threshold is a named
constant below, chosen as a plain, defensible round number and not tuned on results.

Nothing here fetches anything: `SymbolData` is filled by services/screen_preset_service.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from app.analysis.insider_scoring import MIN_NET_BUY_VALUE
from app.analysis.trend import ChartAnalysis
from app.data_providers.base import CompanyOverview, FinancialYear, InsiderActivity

# ---- thresholds (each one a plain round number, see the module docstring)

# Value: a low earnings multiple, in the lower part of the 52-week range, with positive earnings.
# There is no sector median here, so the P/E bar is absolute: 15 is the long-run market average
# multiple that value screens have traditionally used as a ceiling.
VALUE_MAX_PE = 15.0
# "Lower third and a bit": a stock in the bottom 35% of its 52-week range is cheap relative to its
# own recent history, which is the one valuation context we have besides the multiple.
VALUE_MAX_RANGE_POSITION = 0.35

# Growth: the skill's own bars, revenue growth above 15% and earnings growth above 20% a year.
GROWTH_MIN_REVENUE_GROWTH_PCT = 15.0
GROWTH_MIN_EARNINGS_GROWTH_PCT = 20.0

# Quality: needs at least this many reported years to call growth "consistent" at all (the skill
# asks for five; the free financials feed usually holds three to four, so three is the most we
# can ask without making the screen unanswerable for nearly every symbol).
QUALITY_MIN_YEARS = 3

# Special situations: an earnings report inside two weeks, or a move that is large for one day.
# 5% in a day and twice the usual volume are both well outside an ordinary large-cap day.
SPECIAL_EARNINGS_WINDOW_DAYS = 14
SPECIAL_BIG_MOVE_PCT = 5.0
SPECIAL_VOLUME_RATIO = 2.0

# What data a criterion needs, so the service fetches only what the chosen preset uses.
NEED_OVERVIEW = "overview"
NEED_FINANCIALS = "financials"
NEED_CHART = "chart"
NEED_QUOTE = "quote"
NEED_INSIDER = "insider"
NEED_EARNINGS = "earnings"
ALL_NEEDS = (NEED_OVERVIEW, NEED_FINANCIALS, NEED_CHART, NEED_QUOTE, NEED_INSIDER, NEED_EARNINGS)


@dataclass
class SymbolData:
    """One symbol's data for a screen. A field is None when it was not fetched or the fetch
    failed; `financial_years` is empty in that case, and ascending by year otherwise."""

    symbol: str
    today: date
    price: float | None = None
    change_pct_24h: float | None = None
    volume_ratio: float | None = None
    overview: CompanyOverview | None = None
    financial_years: list[FinancialYear] = field(default_factory=list)
    chart: ChartAnalysis | None = None
    insider: InsiderActivity | None = None
    earnings_date: date | None = None


# A criterion's answer: (True/False/None, a short plain-words detail such as "P/E 12.3").
Reading = tuple[bool | None, str]


@dataclass(frozen=True)
class Criterion:
    id: str
    label: str
    needs: tuple[str, ...]
    test: Callable[[SymbolData], Reading]
    required: bool = False


@dataclass(frozen=True)
class UnavailableCriterion:
    """A criterion from the source skill that we cannot answer, and why. Shown, never faked."""

    label: str
    reason: str


@dataclass(frozen=True)
class Preset:
    name: str
    label: str
    description: str
    criteria: tuple[Criterion, ...]
    min_matches: int
    unavailable: tuple[UnavailableCriterion, ...] = ()

    @property
    def needs(self) -> tuple[str, ...]:
        return tuple(n for n in ALL_NEEDS if any(n in c.needs for c in self.criteria))


# ------------------------------------------------------------------ the criteria


def _growth_rates_pct(years: list[FinancialYear], field_name: str) -> list[float | None]:
    """Year-over-year change in `revenue` or `net_income`, oldest first. None where the prior
    year is not positive (a percentage off a zero or negative base says nothing)."""
    rates: list[float | None] = []
    for prior, latest in zip(years, years[1:]):
        base = getattr(prior, field_name)
        rates.append((getattr(latest, field_name) - base) / base * 100.0 if base > 0 else None)
    return rates


def _range_position(overview: CompanyOverview | None, price: float | None) -> float | None:
    """Where the price sits in its 52-week range: 0 = at the low, 1 = at the high."""
    if overview is None or price is None or not overview.week52_high or not overview.week52_low:
        return None
    span = overview.week52_high - overview.week52_low
    return None if span <= 0 else (price - overview.week52_low) / span


def _net_margin(year: FinancialYear) -> float | None:
    return year.net_income / year.revenue if year.revenue > 0 else None


def _pe_low(d: SymbolData) -> Reading:
    pe = d.overview.pe_ratio if d.overview else None
    if pe is None:
        return None, "no P/E available"
    return 0 < pe <= VALUE_MAX_PE, f"P/E {pe:.1f} (bar {VALUE_MAX_PE:g})"


def _profitable_now(d: SymbolData) -> Reading:
    eps = d.overview.eps_ttm if d.overview else None
    if eps is None:
        return None, "no trailing EPS available"
    return eps > 0, f"trailing EPS {eps:.2f}"


def _low_in_range(d: SymbolData) -> Reading:
    pos = _range_position(d.overview, d.price)
    if pos is None:
        return None, "no 52-week range available"
    return pos <= VALUE_MAX_RANGE_POSITION, f"{pos * 100:.0f}% of the way up its 52-week range (bar {VALUE_MAX_RANGE_POSITION * 100:.0f}%)"


def _insider_buying(d: SymbolData) -> Reading:
    activity = d.insider
    if activity is None:
        return None, "no insider data"
    ok = activity.buy_count > 0 and activity.net_value >= MIN_NET_BUY_VALUE
    return ok, f"{activity.buy_count} buy(s), net ${activity.net_value:,.0f} over {activity.window_days} days"


def _insider_net_selling(d: SymbolData) -> Reading:
    activity = d.insider
    if activity is None:
        return None, "no insider data"
    # Routine selling is common (taxes, plans), so this is weak evidence; it is only ever one of
    # several criteria on the short screen, never required.
    return activity.sell_value > activity.buy_value, f"sales ${activity.sell_value:,.0f} vs buys ${activity.buy_value:,.0f}"


def _revenue_growth_high(d: SymbolData) -> Reading:
    rates = _growth_rates_pct(d.financial_years, "revenue")
    if not rates or rates[-1] is None:
        return None, "no revenue growth available"
    return rates[-1] >= GROWTH_MIN_REVENUE_GROWTH_PCT, f"revenue {rates[-1]:+.1f}% YoY (bar {GROWTH_MIN_REVENUE_GROWTH_PCT:g}%)"


def _earnings_growth_high(d: SymbolData) -> Reading:
    rates = _growth_rates_pct(d.financial_years, "net_income")
    if not rates or rates[-1] is None:
        return None, "no earnings growth available (needs a positive prior year)"
    return rates[-1] >= GROWTH_MIN_EARNINGS_GROWTH_PCT, f"net income {rates[-1]:+.1f}% YoY (bar {GROWTH_MIN_EARNINGS_GROWTH_PCT:g}%)"


def _revenue_accelerating(d: SymbolData) -> Reading:
    rates = _growth_rates_pct(d.financial_years, "revenue")
    if len(rates) < 2 or rates[-1] is None or rates[-2] is None:
        return None, "needs three years of revenue"
    return rates[-1] > rates[-2], f"revenue growth {rates[-2]:+.1f}% then {rates[-1]:+.1f}%"


def _bullish_momentum(d: SymbolData) -> Reading:
    if d.chart is None:
        return None, "no price history"
    ok = d.chart.trend == "Bullish" and d.chart.momentum == "Strong"
    return ok, f"{d.chart.trend} trend, {d.chart.momentum.lower()} momentum"


def _not_in_downtrend(d: SymbolData) -> Reading:
    if d.chart is None:
        return None, "no price history"
    return d.chart.trend != "Bearish", f"{d.chart.trend} trend"


def _in_downtrend(d: SymbolData) -> Reading:
    if d.chart is None:
        return None, "no price history"
    return d.chart.trend == "Bearish", f"{d.chart.trend} trend, {d.chart.momentum.lower()} momentum"


def _consistent_revenue_growth(d: SymbolData) -> Reading:
    years = d.financial_years
    if len(years) < QUALITY_MIN_YEARS:
        return None, f"needs {QUALITY_MIN_YEARS} reported years, has {len(years)}"
    ok = all(b.revenue > a.revenue for a, b in zip(years, years[1:]))
    return ok, f"revenue rose every year across {len(years)} years" if ok else f"revenue did not rise every year across {len(years)} years"


def _profitable_every_year(d: SymbolData) -> Reading:
    years = d.financial_years
    if len(years) < QUALITY_MIN_YEARS:
        return None, f"needs {QUALITY_MIN_YEARS} reported years, has {len(years)}"
    ok = all(y.net_income > 0 for y in years)
    return ok, f"net income positive in all {len(years)} years" if ok else "a loss in at least one year"


def _margin_not_shrinking(d: SymbolData) -> Reading:
    years = d.financial_years
    if len(years) < 2:
        return None, "needs two reported years"
    prior, latest = _net_margin(years[-2]), _net_margin(years[-1])
    if prior is None or latest is None:
        return None, "no revenue to compute a margin"
    return latest >= prior, f"net margin {prior * 100:.1f}% then {latest * 100:.1f}%"


def _revenue_declining(d: SymbolData) -> Reading:
    years = d.financial_years
    if len(years) < 2:
        return None, "needs two reported years"
    return years[-1].revenue < years[-2].revenue, f"revenue ${years[-2].revenue:,.0f} then ${years[-1].revenue:,.0f}"


def _growth_slowing(d: SymbolData) -> Reading:
    rates = _growth_rates_pct(d.financial_years, "revenue")
    if len(rates) < 2 or rates[-1] is None or rates[-2] is None:
        return None, "needs three years of revenue"
    return rates[-1] < rates[-2], f"revenue growth {rates[-2]:+.1f}% then {rates[-1]:+.1f}%"


def _margin_compression(d: SymbolData) -> Reading:
    ok, detail = _margin_not_shrinking(d)
    return (None if ok is None else not ok), detail


def _earnings_soon(d: SymbolData) -> Reading:
    if d.earnings_date is None:
        return None, "no upcoming earnings date"
    days = (d.earnings_date - d.today).days
    return 0 <= days <= SPECIAL_EARNINGS_WINDOW_DAYS, f"earnings {d.earnings_date.isoformat()} ({days} days)"


def _big_move(d: SymbolData) -> Reading:
    if d.change_pct_24h is None:
        return None, "no quote"
    return abs(d.change_pct_24h) >= SPECIAL_BIG_MOVE_PCT, f"{d.change_pct_24h:+.1f}% on the day (bar {SPECIAL_BIG_MOVE_PCT:g}%)"


def _volume_spike(d: SymbolData) -> Reading:
    if d.volume_ratio is None:
        return None, "no volume data"
    return d.volume_ratio >= SPECIAL_VOLUME_RATIO, f"volume {d.volume_ratio:.1f}x its 20-day average (bar {SPECIAL_VOLUME_RATIO:g}x)"


# ------------------------------------------------------------------ the presets

PRESETS: tuple[Preset, ...] = (
    Preset(
        name="value",
        label="Value",
        description="A low P/E, cheap against its own 52-week range, and ideally insiders buying.",
        criteria=(
            Criterion("pe_low", f"P/E at or below {VALUE_MAX_PE:g}", (NEED_OVERVIEW,), _pe_low, required=True),
            Criterion("profitable", "Positive trailing earnings", (NEED_OVERVIEW,), _profitable_now),
            Criterion("low_in_range", f"Lower {VALUE_MAX_RANGE_POSITION * 100:.0f}% of its 52-week range", (NEED_OVERVIEW, NEED_QUOTE), _low_in_range),
            Criterion("insider_buying", "Net insider buying over 90 days", (NEED_INSIDER,), _insider_buying),
        ),
        min_matches=2,
        unavailable=(
            UnavailableCriterion("EV/EBITDA below its historical average", "no enterprise value or EBITDA history in the free data"),
            UnavailableCriterion("Free cash flow yield above 5%", "no cash flow statement in the free data"),
            UnavailableCriterion("Price/book below 1.5x", "no book value in the free data"),
            UnavailableCriterion("Dividend yield above the market's", "no dividend data"),
            UnavailableCriterion("P/E below the sector median", "no peer medians; a fixed P/E bar is used instead"),
        ),
    ),
    Preset(
        name="growth",
        label="Growth",
        description="Revenue and earnings growing fast, in a strong uptrend.",
        criteria=(
            Criterion("revenue_growth", f"Revenue growth at least {GROWTH_MIN_REVENUE_GROWTH_PCT:g}% YoY", (NEED_FINANCIALS,), _revenue_growth_high, required=True),
            Criterion("earnings_growth", f"Net income growth at least {GROWTH_MIN_EARNINGS_GROWTH_PCT:g}% YoY", (NEED_FINANCIALS,), _earnings_growth_high),
            Criterion("accelerating", "Revenue growth accelerating", (NEED_FINANCIALS,), _revenue_accelerating),
            Criterion("strong_uptrend", "Bullish trend with strong momentum", (NEED_CHART,), _bullish_momentum),
        ),
        min_matches=2,
        unavailable=(
            UnavailableCriterion("Expanding margins", "only net margin is available, and the quality screen already uses it"),
            UnavailableCriterion("Return on invested capital above 15%", "no balance sheet in the free data"),
            UnavailableCriterion("Net revenue retention above 110%", "company-reported SaaS metric, not in the data"),
        ),
    ),
    Preset(
        name="quality",
        label="Quality",
        description="Steady growth and profits year after year, with margins holding, and not in a downtrend.",
        criteria=(
            Criterion("profitable_every_year", "Profitable in every reported year", (NEED_FINANCIALS,), _profitable_every_year, required=True),
            Criterion("consistent_growth", "Revenue up every reported year", (NEED_FINANCIALS,), _consistent_revenue_growth),
            Criterion("margin_holding", "Net margin stable or expanding", (NEED_FINANCIALS,), _margin_not_shrinking),
            Criterion("not_in_downtrend", "Not in a downtrend", (NEED_CHART,), _not_in_downtrend),
        ),
        min_matches=3,
        unavailable=(
            UnavailableCriterion("Five or more years of growth", f"the free financials feed holds three to four years; {QUALITY_MIN_YEARS} are required here"),
            UnavailableCriterion("Return on equity above 15%", "no balance sheet in the free data"),
            UnavailableCriterion("Low debt/equity", "no balance sheet in the free data"),
            UnavailableCriterion("High free cash flow conversion", "no cash flow statement in the free data"),
            UnavailableCriterion("Insider ownership above 5%", "no ownership data"),
        ),
    ),
    Preset(
        name="short_ideas",
        label="Short ideas",
        description="A downtrend plus weakening fundamentals. A screen for candidates to study, not a signal to short.",
        criteria=(
            Criterion("downtrend", "Bearish daily trend", (NEED_CHART,), _in_downtrend, required=True),
            Criterion("revenue_declining", "Revenue fell year over year", (NEED_FINANCIALS,), _revenue_declining),
            Criterion("growth_slowing", "Revenue growth slowing", (NEED_FINANCIALS,), _growth_slowing),
            Criterion("margin_compression", "Net margin shrinking", (NEED_FINANCIALS,), _margin_compression),
            Criterion("insider_selling", "Insider sales exceed purchases (weak evidence: routine selling is common)", (NEED_INSIDER,), _insider_net_selling),
        ),
        min_matches=2,
        unavailable=(
            UnavailableCriterion("Rising receivables or inventory versus sales", "no balance sheet in the free data"),
            UnavailableCriterion("High short interest", "no short-interest feed wired in"),
            UnavailableCriterion("Accounting red flags (auditor change, restatement)", "no filings-text data"),
            UnavailableCriterion("Valuation premium to peers", "no peer data"),
        ),
    ),
    Preset(
        name="special_situations",
        label="Special situations",
        description="Something is happening: earnings soon, insiders buying, or an outsized move on unusual volume.",
        criteria=(
            Criterion("earnings_soon", f"Earnings within {SPECIAL_EARNINGS_WINDOW_DAYS} days", (NEED_EARNINGS,), _earnings_soon),
            Criterion("insider_buying", "Net insider buying over 90 days", (NEED_INSIDER,), _insider_buying),
            Criterion("big_move", f"Moved {SPECIAL_BIG_MOVE_PCT:g}% or more today", (NEED_QUOTE,), _big_move),
            Criterion("volume_spike", f"Volume at least {SPECIAL_VOLUME_RATIO:g}x the 20-day average", (NEED_QUOTE,), _volume_spike),
        ),
        min_matches=2,
        unavailable=(
            UnavailableCriterion("Recent IPOs and SPACs with lockup expirations", "no IPO or lockup calendar"),
            UnavailableCriterion("Spin-offs in the last 12 months", "no corporate-actions feed"),
            UnavailableCriterion("Emerging from restructuring", "no filings-text data"),
            UnavailableCriterion("Activist involvement", "no 13D feed wired into the screen"),
            UnavailableCriterion("Management changes", "no filings-text data"),
        ),
    ),
)

_BY_NAME = {p.name: p for p in PRESETS}


def get_preset(name: str) -> Preset | None:
    return _BY_NAME.get(name)


# ------------------------------------------------------------------ evaluation


@dataclass
class CriterionResult:
    id: str
    label: str
    ok: bool | None
    detail: str


@dataclass
class SymbolEvaluation:
    symbol: str
    # "match" | "no_match" | "unjudged" (a required criterion could not be answered)
    status: str
    results: list[CriterionResult]


def evaluate(preset: Preset, data: SymbolData) -> SymbolEvaluation:
    """Run every criterion of `preset` on one symbol's data and decide match / no match / unjudged."""
    results = []
    for criterion in preset.criteria:
        try:
            ok, detail = criterion.test(data)
        except Exception as exc:  # noqa: BLE001 - a malformed value must not sink the whole screen
            ok, detail = None, f"could not be evaluated ({type(exc).__name__})"
        results.append(CriterionResult(criterion.id, criterion.label, ok, detail))

    required = [r for r, c in zip(results, preset.criteria) if c.required]
    if any(r.ok is False for r in required):
        status = "no_match"
    elif any(r.ok is None for r in required):
        status = "unjudged"
    else:
        status = "match" if sum(1 for r in results if r.ok) >= preset.min_matches else "no_match"
    return SymbolEvaluation(data.symbol, status, results)
