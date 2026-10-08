"""The facts the committee works from, gathered once per run and computed without any AI.

One price history is required (without it there is nothing to discuss, and the run fails with the
data layer's own error). Everything else is best effort: a section whose data could not be fetched
is marked unavailable and the matching analyst is skipped, so no AI call is spent writing a report
about nothing and nothing is ever made up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlmodel import Session

from app.analysis.expected_move import compute_expected_move_pct, days_to_expiration
from app.analysis.ground_truth import GroundTruthSnapshot, build_ground_truth, render_ground_truth
from app.analysis.indicators import latest_atr
from app.analysis.trend import analyze_chart
from app.committee.context import (
    market_context_lines,
    narrative_lines,
    options_interest_line,
    ownership_lines,
    valuation_lines,
)
from app.committee.prompts import untrusted_block
from app.data_providers.base import AllProvidersFailedError, DataProvider

logger = logging.getLogger(__name__)

ATR_PERIOD = 14  # the same period the rules engine uses for its stop floor
RECENT_CLOSES_SHOWN = 5
NEWS_ITEMS = 8
EARNINGS_QUARTERS_SHOWN = 6
FILINGS_8K_WINDOW_DAYS = 14
CONGRESS_WINDOW_DAYS = 45
FUNDS_SHOWN = 6
OWNERSHIP_WINDOW_DAYS = 30


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


def _pct(value: float | None) -> str:
    return f"{value:+.1f}%" if value is not None else "not available"


def _financial_trend_lines(financial_years: list) -> list[str]:
    """Revenue growth and net margin per reported year: computed here, never by the AI."""
    lines: list[str] = []
    prior_revenue = None
    for year in financial_years[-4:]:
        growth = (year.revenue - prior_revenue) / prior_revenue * 100 if prior_revenue else None
        margin = year.net_income / year.revenue * 100 if year.revenue else None
        lines.append(
            f"Year {year.year}: revenue {_big(year.revenue)} (growth {_pct(growth)}), "
            f"net income {_big(year.net_income)} (net margin {_pct(margin)})"
        )
        prior_revenue = year.revenue
    return lines


def _earnings_lines(earnings_date, estimate, history: list) -> list[str]:
    lines = [f"Next earnings date: {earnings_date.isoformat() if earnings_date else 'not available'}"]
    if estimate is not None:
        lines.append(
            f"Consensus for that report: EPS {_money(estimate.eps_estimate)}, revenue {_big(estimate.revenue_estimate)}"
            + (f" ({estimate.fiscal_period_label})" if estimate.fiscal_period_label else "")
        )
    for entry in history[:EARNINGS_QUARTERS_SHOWN]:
        lines.append(
            f"Reported {entry.date.isoformat()}: EPS estimate {_money(entry.eps_estimate)}, actual {_money(entry.eps_actual)}, "
            f"surprise {_pct(entry.surprise_pct)}"
        )
    return lines


def _options_text(options, atr_pct: float | None) -> str | None:
    if options is None:
        return None
    lines = [
        f"Nearest expiry at least a week out: {options.expiration}",
        "Put/call volume ratio: "
        + (
            f"{options.put_call_volume_ratio:.2f} (under 0.7 call-heavy, over 1.0 put-heavy)"
            if options.put_call_volume_ratio is not None
            else "not available"
        ),
        "At-the-money implied volatility: "
        + (f"{options.atm_implied_volatility * 100:.1f}%" if options.atm_implied_volatility is not None else "not available"),
    ]
    days = days_to_expiration(options.expiration)
    if options.atm_implied_volatility is not None and days:
        move = compute_expected_move_pct(options.atm_implied_volatility, days)
        if move is not None:
            lines.append(f"Options-implied move to that expiry ({days} days): about {move:.1f}%")
    if atr_pct is not None:
        lines.append(f"The stock's normal daily range (ATR) is {atr_pct:.1f}% of its price")
    return "\n".join(lines)


def _smart_money_lines(session: Session | None, symbol: str) -> tuple[list[str], list[str]]:
    """(smart-money lines, 8-K filing lines) from the dated archive. A source that was never loaded says so
    plainly instead of reading as "nothing happened". Every reader filters to what was public at the
    current moment."""
    if session is None:
        return [], []
    from app.data_providers.sec_8k import filings_8k_as_of, has_8k_data
    from app.knowledge import FactKind, facts_known_as_of
    from app.knowledge.congress_trades import has_congress_data, net_buyers_as_of
    from app.knowledge.fund_holdings import fund_holders_of_symbol, ownership_filings_as_of
    from app.signals.finra import reading_is_fresh, short_volume_ratio_as_of

    lines: list[str] = []
    if has_congress_data(session):
        net = net_buyers_as_of(session, symbol, window_days=CONGRESS_WINDOW_DAYS)
        lines.append(
            f"Congress (House, by filing date, last {CONGRESS_WINDOW_DAYS} days): {net.buyers} member(s) bought, "
            f"{net.sellers} sold. Reports lag the trades by up to 45 days."
        )
    else:
        lines.append("Congress trades: none stored (never loaded).")
    if facts_known_as_of(session, FactKind.FUND_FILING, limit=1):
        holders = fund_holders_of_symbol(session, symbol)[:FUNDS_SHOWN]
        if holders:
            for h in holders:
                change = f", {_pct(h.change_pct)} vs prior quarter" if h.change_pct is not None else ""
                lines.append(
                    f"Fund {h.manager or h.cik}: {h.status.replace('_', ' ')}{change} ({h.shares:,.0f} shares); "
                    "13F data lags up to 45 days."
                )
        else:
            lines.append("Followed funds: none reported a position in this stock.")
    else:
        lines.append("Fund 13F filings: none stored (never loaded).")
    if facts_known_as_of(session, FactKind.OWNERSHIP_FILING, symbol=symbol, limit=1):
        new_13d = [
            r
            for r in ownership_filings_as_of(session, symbol, window_days=OWNERSHIP_WINDOW_DAYS, schedule="13D")
            if not r.is_amendment
        ]
        if new_13d:
            first = new_13d[0]
            pct = f" ({first.percent:g}%)" if first.percent is not None else ""
            lines.append(f"New Schedule 13D in the last {OWNERSHIP_WINDOW_DAYS} days: {first.filer_name or 'a holder'}{pct}.")
        else:
            lines.append(f"No new Schedule 13D in the last {OWNERSHIP_WINDOW_DAYS} days.")
    reading = short_volume_ratio_as_of(session, symbol)
    if reading is not None and reading_is_fresh(reading):
        base = f", its own baseline {reading.baseline_ratio:.0%}" if reading.baseline_ratio is not None else ""
        lines.append(
            f"FINRA short-sale volume: {reading.recent_ratio:.0%} of recent volume{base}. This is short volume, not "
            "short interest: market makers sell short all day, so only a clear rise over the baseline means anything."
        )
    else:
        lines.append("FINRA short volume: no fresh reading stored.")

    filing_lines: list[str] = []
    if has_8k_data(session, symbol):
        for f in filings_8k_as_of(session, symbol, window_days=FILINGS_8K_WINDOW_DAYS)[:6]:
            titles = "; ".join(f.item_titles) or ", ".join(f.items)
            filing_lines.append(f"8-K filed {f.known_at.date().isoformat()}: {titles}")
    return lines, filing_lines


def _with_lines(text: str | None, extra: list[str]) -> str | None:
    """`text` with `extra` lines added, or just the lines when there was no text. None when there is neither."""
    if not extra:
        return text
    return "\n".join(([text] if text else []) + extra)


def gather_data(symbol: str, data_provider: DataProvider, session: Session | None = None) -> DataPack:
    """Fetch and compute. Raises AllProvidersFailedError only when no price history exists at all.
    `session` lets the dated archive (Congress, funds, 13D, short volume, 8-K) be read; without one
    those parts say they are not available."""
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

    estimate = None
    try:
        estimate = data_provider.get_earnings_estimate(symbol)
    except (AllProvidersFailedError, NotImplementedError):
        pass
    history: list = []
    try:
        history = list(data_provider.get_earnings_history(symbol, limit=EARNINGS_QUARTERS_SHOWN))
    except (AllProvidersFailedError, NotImplementedError):
        pass
    options = None
    try:
        options = data_provider.get_options_summary(symbol)
    except (AllProvidersFailedError, NotImplementedError):
        pass

    volume_ratio = None
    if quote is not None and quote.avg_volume_20d:
        volume_ratio = quote.volume / quote.avg_volume_20d
    atr = latest_atr(ohlcv, ATR_PERIOD)
    atr_pct = atr / chart.price * 100 if atr and chart.price else None
    snapshot = build_ground_truth(
        symbol,
        chart,
        quote_price=quote.price if quote else None,
        change_pct=quote.change_pct_24h if quote else None,
        volume_ratio=volume_ratio,
        atr=atr,
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
    extra_market = market_context_lines(symbol, data_provider, ohlcv)
    if extra_market:
        market += "\n" + "\n".join(extra_market)

    smart_lines, filing_lines = _smart_money_lines(session, symbol)
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
        lines += _financial_trend_lines(financial_years)
        lines += _earnings_lines(earnings_date, estimate, history)
        lines += valuation_lines(data_provider, symbol, estimate, history)
        fundamentals = "\n".join(lines)

    narrative, narrative_has_content = narrative_lines(session, symbol)
    news_lines = [f"{n.headline} ({n.source}, {n.published_at})" for n in news] + filing_lines
    if news_lines or narrative_has_content:
        news_lines += narrative  # the standing statements ride along only when there is something to read
    news_text = untrusted_block(news_lines) if news_lines else None

    insider_parts: list[str] = []
    if insider is not None:
        insider_parts.append(
            f"Insiders (SEC Form 4), last {insider.window_days} days. Open-market purchases: {insider.buy_count} "
            f"(total {_big(insider.buy_value)}). Open-market sales: {insider.sell_count} "
            f"(total {_big(insider.sell_value)}). Net: {_big(insider.net_value)}."
        )
    if session is not None:
        from app.analysis.insider_scoring import insider_sell_split

        split = insider_sell_split(session, symbol, insider.window_days if insider is not None else 90)
        if split is not None:
            insider_parts.append(
                f"Of those sales, {split.discretionary_count} (total {_big(split.discretionary_value)}) were made outside "
                f"10b5-1 plans, so by the insider's own choice, and {split.plan_count} (total {_big(split.plan_value)}) "
                "were scheduled in advance under 10b5-1 plans."
            )
    insider_parts += ownership_lines(session, symbol)
    insider_parts += smart_lines
    insider_text = "\n".join(insider_parts) if insider_parts else None

    return DataPack(
        symbol=symbol,
        snapshot=snapshot,
        ground_truth=rendered,
        sections={
            "market": market,
            "fundamentals": fundamentals,
            "news": news_text,
            "insider": insider_text,
            "options": _with_lines(_options_text(options, atr_pct), options_interest_line(data_provider, symbol)),
        },
    )
