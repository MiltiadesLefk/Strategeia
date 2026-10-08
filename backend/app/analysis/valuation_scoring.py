"""Valuation at the extremes as a scored confluence check.

Only the clear cases move the score, because a P/E alone says little for a swing trade:

  * Cheap: a positive P/E of at most CHEAP_PE.
  * Expensive: a P/E above EXPENSIVE_PE, or negative earnings per share, unless revenue is growing at
    least FAST_GROWTH_PCT a year (a fast grower is allowed a high multiple; then the check stays quiet).

Direction-signed: cheap supports a long (+1) and argues against a short; expensive does the opposite. No
P/E, no direction, or anything in between scores 0. Rules only, no AI. The full valuation work (DCF,
peers) stays on the Analysis page and the Committee's fundamentals report.
"""

from __future__ import annotations

from app.data_providers.base import CompanyOverview, FinancialYear

VALUATION_SCORE_CAP = 2
# DCF: the projected value per share against the price. At least this much upside is cheap, this much downside
# is expensive. Peers: the stock's P/E against the median P/E of its sector peers, by this ratio.
DCF_UPSIDE_PCT = 25.0
DCF_DOWNSIDE_PCT = -25.0
PEER_CHEAP_RATIO = 0.75
PEER_EXPENSIVE_RATIO = 1.33
CHEAP_PE = 15.0
EXPENSIVE_PE = 60.0
FAST_GROWTH_PCT = 30.0


def _revenue_growth_pct(financial_years: list[FinancialYear]) -> float | None:
    if len(financial_years) < 2 or not financial_years[-2].revenue:
        return None
    return (financial_years[-1].revenue - financial_years[-2].revenue) / financial_years[-2].revenue * 100


def score_dcf_and_peers(direction: str | None, valuation) -> tuple[int, list[str]]:
    """The valuation page's DCF and peer comparison as one vote: cheap on either (and not expensive on the other)
    supports a long; expensive on either (and not cheap on the other) supports a short. `valuation` is the
    `ValuationResponse` the Valuation tab uses (None or unavailable scores 0)."""
    if direction not in ("long", "short") or valuation is None or not getattr(valuation, "available", False):
        return 0, []
    cheap = expensive = 0
    notes: list[str] = []
    dcf = getattr(valuation, "dcf", None)
    if dcf is not None and dcf.upside_pct is not None:
        if dcf.upside_pct >= DCF_UPSIDE_PCT:
            cheap += 1
            notes.append(f"DCF value is {dcf.upside_pct:+.0f}% against the price")
        elif dcf.upside_pct <= DCF_DOWNSIDE_PCT:
            expensive += 1
            notes.append(f"DCF value is {dcf.upside_pct:+.0f}% against the price")
    comps = getattr(valuation, "comps", None)
    pe = next((m for m in (comps.multiples if comps else []) if "P/E" in m.name and m.subject and m.median and m.count >= 3), None)
    if pe is not None:
        ratio = pe.subject / pe.median
        if ratio <= PEER_CHEAP_RATIO:
            cheap += 1
            notes.append(f"P/E {pe.subject:.1f} is {ratio:.2f}x its peers' median {pe.median:.1f}")
        elif ratio >= PEER_EXPENSIVE_RATIO:
            expensive += 1
            notes.append(f"P/E {pe.subject:.1f} is {ratio:.2f}x its peers' median {pe.median:.1f}")
    net = cheap - expensive
    if net == 0:
        return 0, []
    supports = (net > 0) == (direction == "long")
    verb = "supports" if supports else "argues against"
    word = "cheap" if net > 0 else "expensive"
    return (1 if supports else -1), [f"valuation: {word} on {' and '.join(notes)}, which {verb} a {direction}"]


def score_valuation(
    direction: str | None,
    overview: CompanyOverview | None,
    financial_years: list[FinancialYear],
    valuation=None,
) -> tuple[int, list[str]]:
    """P/E at the extremes (one point) plus the DCF and peer comparison (one point), up to VALUATION_SCORE_CAP."""
    pe_points, pe_reasons = _score_pe_extremes(direction, overview, financial_years)
    dcf_points, dcf_reasons = score_dcf_and_peers(direction, valuation)
    total = max(-VALUATION_SCORE_CAP, min(VALUATION_SCORE_CAP, pe_points + dcf_points))
    return total, pe_reasons + dcf_reasons


def _score_pe_extremes(
    direction: str | None, overview: CompanyOverview | None, financial_years: list[FinancialYear]
) -> tuple[int, list[str]]:
    if direction not in ("long", "short") or overview is None:
        return 0, []
    pe, eps = overview.pe_ratio, overview.eps_ttm
    growth = _revenue_growth_pct(financial_years)
    if pe is not None and 0 < pe <= CHEAP_PE:
        cheap, text = True, f"cheap on earnings (P/E {pe:.1f})"
    elif (pe is not None and pe > EXPENSIVE_PE) or (eps is not None and eps < 0):
        if growth is not None and growth >= FAST_GROWTH_PCT:
            return 0, []
        cheap = False
        text = f"expensive on earnings (P/E {pe:.1f})" if pe is not None and pe > 0 else "no earnings (negative EPS)"
    else:
        return 0, []
    supports = cheap == (direction == "long")
    verb = "supports" if supports else "argues against"
    points = 1 if supports else -1
    return points, [f"valuation: {text}, which {verb} a {direction}"]
