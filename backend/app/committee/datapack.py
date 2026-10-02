"""The facts the committee works from, gathered once per run and computed without any AI.

One price history is required (without it there is nothing to discuss, and the run fails with the
data layer's own error). Everything else is best effort: a section whose data could not be fetched
is marked unavailable and the matching analyst is skipped, so no AI call is spent writing a report
about nothing and nothing is ever made up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.analysis.ground_truth import GroundTruthSnapshot, build_ground_truth, render_ground_truth
from app.analysis.indicators import latest_atr
from app.analysis.trend import analyze_chart
from app.committee.prompts import untrusted_block
from app.data_providers.base import AllProvidersFailedError, DataProvider

logger = logging.getLogger(__name__)

ATR_PERIOD = 14  # the same period the rules engine uses for its stop floor
RECENT_CLOSES_SHOWN = 5
NEWS_ITEMS = 8


@dataclass
class DataPack:
    symbol: str
    snapshot: GroundTruthSnapshot
    ground_truth: str  # the rendered block, without the rule-based verdict
    sections: dict[str, str | None] = field(default_factory=dict)  # analyst key -> data text; None = not available

    def all_data_text(self) -> str:
        """Every figure given to any prompt, for the figure checker."""
        return "\n".join(text for text in self.sections.values() if text)


def _money(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "not available"


def _big(value: float | None) -> str:
    if value is None:
        return "not available"
    for limit, suffix in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million")):
        if abs(value) >= limit:
            return f"${value / limit:,.2f} {suffix}"
    return f"${value:,.0f}"


def gather_data(symbol: str, data_provider: DataProvider) -> DataPack:
    """Fetch and compute. Raises AllProvidersFailedError only when no price history exists at all."""
    ohlcv = data_provider.get_ohlcv(symbol, period="1y", interval="1d")
    chart = analyze_chart(ohlcv)

    quote = None
    try:
        quote = data_provider.get_quote(symbol)
    except AllProvidersFailedError:
        pass
    overview = None
    try:
        overview = data_provider.get_company_overview(symbol)
    except AllProvidersFailedError:
        pass
    financial_years: list = []
    try:
        financial_years = list(data_provider.get_financials(symbol).years)
    except AllProvidersFailedError:
        pass
    news: list = []
    try:
        news = list(data_provider.get_news(symbol, limit=NEWS_ITEMS))
    except AllProvidersFailedError:
        pass
    earnings_date = None
    try:
        earnings_date = data_provider.get_earnings_date(symbol)
    except AllProvidersFailedError:
        pass
    insider = None
    try:
        insider = data_provider.get_insider_activity(symbol)
    except (AllProvidersFailedError, NotImplementedError):
        pass

    volume_ratio = None
    if quote is not None and quote.avg_volume_20d:
        volume_ratio = quote.volume / quote.avg_volume_20d
    snapshot = build_ground_truth(
        symbol,
        chart,
        quote_price=quote.price if quote else None,
        change_pct=quote.change_pct_24h if quote else None,
        volume_ratio=volume_ratio,
        atr=latest_atr(ohlcv, ATR_PERIOD),
        week52_low=overview.week52_low if overview else None,
        week52_high=overview.week52_high if overview else None,
        earnings_date=earnings_date,
    )
    rendered = render_ground_truth(snapshot, include_instruction=True, include_rule_verdict=False)

    closes = [float(c) for c in ohlcv["close"].tail(RECENT_CLOSES_SHOWN)]
    market = (
        f"Last {len(closes)} daily closes, oldest first: " + ", ".join(_money(c) for c in closes) + "\n"
        "Indicators are in the GROUND TRUTH block above."
    )

    fundamentals: str | None = None
    if overview is not None or financial_years:
        lines = []
        if overview is not None:
            lines += [
                f"Company: {overview.name}",
                f"Market value: {_big(overview.market_cap)}",
                f"P/E ratio: {overview.pe_ratio:.1f}" if overview.pe_ratio is not None else "P/E ratio: not available",
                f"Revenue, last twelve months: {_big(overview.revenue_ttm)}",
                f"Earnings per share, last twelve months: {_money(overview.eps_ttm)}",
            ]
        for year in financial_years[-4:]:
            lines.append(f"Year {year.year}: revenue {_big(year.revenue)}, net income {_big(year.net_income)}")
        fundamentals = "\n".join(lines)

    news_text = untrusted_block([f"{n.headline} ({n.source}, {n.published_at})" for n in news]) if news else None

    insider_text = None
    if insider is not None:
        insider_text = (
            f"Window: last {insider.window_days} days. Open-market purchases: {insider.buy_count} "
            f"(total {_big(insider.buy_value)}). Open-market sales: {insider.sell_count} "
            f"(total {_big(insider.sell_value)}). Net: {_big(insider.net_value)}."
        )

    return DataPack(
        symbol=symbol,
        snapshot=snapshot,
        ground_truth=rendered,
        sections={"market": market, "fundamentals": fundamentals, "news": news_text, "insider": insider_text},
    )
