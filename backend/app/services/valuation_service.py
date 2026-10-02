"""Valuation tab: a simple discounted-earnings projection and a peer-multiple table.

Read-only and informational. It never touches a trade plan: scores, direction and
size come from the rule engine alone. A missing input stays missing ("not
available"); nothing is estimated to fill a gap. EV/EBITDA is not offered because
the free sources give no debt or EBITDA figures to build it from.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from app.analysis import valuation as val
from app.data_providers.base import AllProvidersFailedError, DataProvider
from app.data_providers.universe import get_sector, load_bundled_universe
from app.llm_providers.base import LLMProvider
from app.llm_providers.factory import generate_with_fallback
from app.schemas.valuation_schemas import CompsResult, DcfAssumptions, PeerRow, ValuationResponse

logger = logging.getLogger(__name__)

# Peers come from the same sector of the bundled list, in symbol order, so the
# table is the same every time and the number of provider calls is bounded.
MAX_PEERS = 8
PEER_FETCH_WORKERS = 4

NOTE_EARNINGS_BASIS = (
    "The projection discounts net income, not free cash flow (the free sources have no cash-flow statement), "
    "so treat it as a rough equity-value estimate."
)
NOTE_NO_EVEBITDA = "EV/EBITDA is not shown: the free sources do not supply debt or EBITDA."
NOTE_INFO_ONLY = "Informational only. This does not change any trade plan's score, direction or size."


def _peer_row(provider: DataProvider, symbol: str, name: str) -> PeerRow | None:
    try:
        o = provider.get_company_overview(symbol)
    except Exception:  # one peer failing must not sink the table
        logger.info("valuation: no overview for peer %s", symbol)
        return None
    ps = o.market_cap / o.revenue_ttm if o.market_cap and o.revenue_ttm and o.revenue_ttm > 0 else None
    return PeerRow(symbol=symbol, name=o.name or name, market_cap=o.market_cap, pe_ratio=o.pe_ratio, price_to_sales=ps)


def build_comps(
    provider: DataProvider, symbol: str, pe: float | None, ps: float | None, eps: float | None, sales_per_share: float | None
) -> CompsResult:
    sector = get_sector(symbol)
    if not sector or sector == "Crypto":
        return CompsResult(sector=sector, reason="No peer group: this symbol has no stock sector in the bundled list.")
    candidates = [e for e in load_bundled_universe() if e.sector == sector and e.symbol != symbol][:MAX_PEERS]
    if not candidates:
        return CompsResult(sector=sector, reason="No other companies in this sector were found in the bundled list.")
    with ThreadPoolExecutor(max_workers=PEER_FETCH_WORKERS) as pool:
        rows = list(pool.map(lambda e: _peer_row(provider, e.symbol, e.name), candidates))
    peers = [r for r in rows if r is not None]
    if not peers:
        return CompsResult(sector=sector, reason="Peer data could not be loaded right now.")
    return CompsResult(sector=sector, peers=peers, multiples=val.peer_summaries(peers, pe, ps, eps, sales_per_share))


def _fallback_summary(r: ValuationResponse) -> str:
    parts = []
    if r.dcf and r.dcf.value_per_share is not None and r.price is not None:
        tail = f" ({r.dcf.upside_pct:+.1f}%)." if r.dcf.upside_pct is not None else "."
        parts.append(f"The discounted-earnings estimate is {r.dcf.value_per_share:.2f} per share against a price of {r.price:.2f}{tail}")
    if r.comps:
        for m in r.comps.multiples:
            if m.median is not None:
                parts.append(f"The peer median {m.name} is {m.median:.1f} across {m.count} companies.")
    parts.append("The result depends heavily on the assumptions.")
    return " ".join(parts)


def _build_prompt(r: ValuationResponse) -> str:
    facts = [f"Company: {r.name} ({r.symbol}). Price: {r.price}."]
    if r.dcf:
        a = r.dcf.assumptions
        facts.append(
            f"Discounted-earnings estimate: {r.dcf.value_per_share} per share, upside {r.dcf.upside_pct}%. "
            f"Assumptions: growth {a.growth_pct}%, net margin {a.net_margin_pct}%, discount {a.discount_rate_pct}%, "
            f"terminal growth {a.terminal_growth_pct}%, {a.years} years. Terminal value share {r.dcf.terminal_share_pct:.0f}%."
        )
    if r.comps:
        for m in r.comps.multiples:
            facts.append(f"{m.name}: subject {m.subject}, peer median {m.median} (n={m.count}), implied price {m.implied_price}.")
    return (
        "Write one short plain-English paragraph (under 90 words) describing what these valuation figures show. "
        "Use ONLY the numbers below; add no facts, no forecasts, no recommendation, no buy/sell language. "
        "Say that the result depends heavily on the assumptions.\n\nFACTS:\n" + "\n".join(facts)
    )


def get_valuation(
    provider: DataProvider,
    symbol: str,
    *,
    growth_pct: float | None = None,
    net_margin_pct: float | None = None,
    discount_rate_pct: float = val.DEFAULT_DISCOUNT_PCT,
    terminal_growth_pct: float = val.DEFAULT_TERMINAL_PCT,
    years: int = val.DEFAULT_YEARS,
    llm: LLMProvider | None = None,
) -> ValuationResponse:
    def empty(reason: str) -> ValuationResponse:
        return ValuationResponse(symbol=symbol, name=symbol, available=False, reason=reason)

    if symbol.endswith("-USD"):
        return empty("Crypto has no earnings to value.")
    try:
        overview = provider.get_company_overview(symbol)
    except AllProvidersFailedError:
        return empty("Company data could not be loaded right now.")
    try:
        financials = provider.get_financials(symbol)
    except AllProvidersFailedError:
        financials = None
    try:
        price: float | None = provider.get_quote(symbol).price
    except AllProvidersFailedError:
        price = None

    years_data = financials.years if financials else []
    revenues = [y.revenue for y in years_data]
    latest = years_data[-1] if years_data else None
    if overview.revenue_ttm and overview.revenue_ttm > 0:
        base_revenue: float | None = overview.revenue_ttm
    else:
        base_revenue = latest.revenue if latest and latest.revenue > 0 else None
    shares = overview.market_cap / price if overview.market_cap and price and price > 0 else None
    ps = overview.market_cap / overview.revenue_ttm if overview.market_cap and overview.revenue_ttm and overview.revenue_ttm > 0 else None
    sales_per_share = base_revenue / shares if base_revenue and shares else None

    resp = ValuationResponse(
        symbol=symbol, name=overview.name or symbol, available=True, price=price,
        notes=[NOTE_EARNINGS_BASIS, NOTE_NO_EVEBITDA, NOTE_INFO_ONLY],
    )

    g_default, g_src = val.default_growth_pct(revenues)
    m_default = latest.net_income / latest.revenue * 100 if latest and latest.revenue > 0 else None
    if base_revenue is None:
        resp.notes.append("DCF not available: no revenue figure was found.")
    else:
        growth = growth_pct if growth_pct is not None else g_default
        margin = net_margin_pct if net_margin_pct is not None else m_default
        if growth is None or margin is None:
            resp.notes.append("DCF not available: growth or margin could not be taken from reported figures; enter them yourself.")
        else:
            problem = val.validate_inputs(growth, margin, discount_rate_pct, terminal_growth_pct, years)
            if problem:
                resp.notes.append(f"DCF not computed: {problem}")
            else:
                assumptions = DcfAssumptions(
                    growth_pct=growth, net_margin_pct=margin, discount_rate_pct=discount_rate_pct,
                    terminal_growth_pct=terminal_growth_pct, years=years,
                    growth_default_pct=g_default, margin_default_pct=m_default, growth_source=g_src,
                    margin_source="latest reported year's net income / revenue" if m_default is not None else "not available",
                )
                resp.dcf = val.run_dcf(base_revenue, assumptions, shares, price)
                if shares is None:
                    resp.notes.append("No per-share value: market cap or price was missing.")
                if margin <= 0:
                    resp.notes.append("The net margin is zero or negative, so the projection has no positive value.")

    resp.comps = build_comps(provider, symbol, overview.pe_ratio, ps, overview.eps_ttm, sales_per_share)
    if not resp.dcf and not resp.comps.multiples:
        resp.available = False
        resp.reason = "Not enough data to value this company."
        return resp

    if llm is not None:
        out = generate_with_fallback(llm, _build_prompt(resp), _fallback_summary(resp))
        resp.summary = out.text
        resp.summary_source = "ai" if out.provider != "none" else "rules"
    return resp
