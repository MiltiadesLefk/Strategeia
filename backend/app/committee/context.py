"""The rest of what the rules look at, as plain lines for the committee's data pack (no AI, no scoring).

Every helper is best effort and returns no lines when its source is missing or fails, so a gap in one source
never stops a run; a source that has nothing to say is simply absent from that report. The text here only
restates numbers the app computed (the same functions the rules score from), so the committee reads what the
rules read, plus the narrative sources the rules cannot score (Fed items, posts, labelled headlines).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
from sqlmodel import Session

from app.analysis.macro_calendar import ALL_MACRO_EVENTS
from app.analysis.market_confirmation import MARKET_PROXY_SYMBOL, VIX_PROXY_SYMBOL
from app.analysis.options_consensus_evidence import consensus_growth_pct, open_interest_ratio
from app.analysis.price_evidence import (
    RELATIVE_STRENGTH_DAYS,
    VOLUME_TREND_DAYS,
    relative_strength,
    up_down_volume_ratio,
)
from app.analysis.trend import analyze_chart
from app.data_providers.base import DataProvider

logger = logging.getLogger(__name__)

MACRO_LOOKAHEAD_DAYS = 14
FED_ITEMS_SHOWN = 4
FED_LOOKBACK_DAYS = 10
LABELLED_HEADLINES_SHOWN = 6


def _safe(label: str, build) -> list[str]:
    try:
        return build()
    except Exception:  # noqa: BLE001 - one source must never stop a run
        logger.debug("committee context %s unavailable", label, exc_info=True)
        return []


def market_context_lines(symbol: str, data_provider: DataProvider, ohlcv: pd.DataFrame) -> list[str]:
    """Weekly chart, the broad market, the VIX, relative strength and the volume trend."""

    def build() -> list[str]:
        lines: list[str] = []
        try:
            weekly = analyze_chart(data_provider.get_ohlcv(symbol, period="2y", interval="1wk"))
            lines.append(f"Weekly chart: {weekly.trend} trend, {weekly.momentum.lower()} momentum, RSI {weekly.rsi14:.0f}")
        except Exception:  # noqa: BLE001
            pass
        market = None
        if symbol != MARKET_PROXY_SYMBOL:
            try:
                market = data_provider.get_ohlcv(MARKET_PROXY_SYMBOL, period="1y", interval="1d")
                spy = analyze_chart(market)
                lines.append(f"Broad market ({MARKET_PROXY_SYMBOL}): {spy.trend} trend, {spy.momentum.lower()} momentum")
            except Exception:  # noqa: BLE001
                market = None
        try:
            vix = float(data_provider.get_ohlcv(VIX_PROXY_SYMBOL, period="1mo", interval="1d")["close"].iloc[-1])
            lines.append(f"VIX (market fear gauge): {vix:.1f} (25 or more is a fearful market)")
        except Exception:  # noqa: BLE001
            pass
        rs = relative_strength(ohlcv, market)
        if rs is not None:
            lines.append(
                f"{RELATIVE_STRENGTH_DAYS}-day return: {rs[0]:+.1f}% for the stock against {rs[1]:+.1f}% for the market, "
                f"a gap of {rs[2]:+.1f} points"
            )
        ratio = up_down_volume_ratio(ohlcv)
        if ratio is not None:
            lean = "accumulation (buyers heavier)" if ratio >= 1.3 else "distribution (sellers heavier)" if ratio <= 1 / 1.3 else "balanced"
            lines.append(f"Volume trend over {VOLUME_TREND_DAYS} days: up-day volume is {ratio:.2f}x down-day volume, {lean}")
        return lines

    return _safe("market", build)


def valuation_lines(data_provider: DataProvider, symbol: str, estimate, history: list) -> list[str]:
    """The DCF, the peer comparison and the consensus growth the valuation page shows."""

    def build() -> list[str]:
        from app.services.valuation_service import get_valuation

        lines: list[str] = []
        resp = get_valuation(data_provider, symbol)
        if resp.available and resp.dcf is not None:
            d = resp.dcf
            per_share = f"${d.value_per_share:,.2f}" if d.value_per_share is not None else "not available"
            upside = f" ({d.upside_pct:+.0f}% against the price)" if d.upside_pct is not None else ""
            a = d.assumptions
            lines.append(
                f"Discounted-earnings estimate (rough, from net income): {per_share} per share{upside}. Assumptions: growth "
                f"{a.growth_pct:.1f}% a year, net margin {a.net_margin_pct:.1f}%, discount rate {a.discount_rate_pct:.1f}%, "
                f"terminal growth {a.terminal_growth_pct:.1f}%, {a.years} years."
            )
        if resp.available and resp.comps is not None and resp.comps.multiples:
            for m in resp.comps.multiples:
                if m.subject is not None and m.median is not None:
                    lines.append(
                        f"Peers ({resp.comps.sector}, {m.count} with data): {m.name} {m.subject:.1f} against a peer median of "
                        f"{m.median:.1f} (range {m.low:.1f} to {m.high:.1f})"
                    )
        growth = consensus_growth_pct(estimate, history)
        if growth is not None:
            lines.append(
                f"Consensus EPS for the next report is ${growth[1]:.2f}, {growth[0]:+.1f}% against the last reported ${growth[2]:.2f}"
            )
        return lines

    return _safe("valuation", build)


def options_interest_line(data_provider: DataProvider, symbol: str) -> list[str]:
    def build() -> list[str]:
        reading = open_interest_ratio(data_provider.get_options_chain(symbol))
        if reading is None:
            return []
        return [
            f"Open interest: {reading[1]:,.0f} puts against {reading[2]:,.0f} calls, a put/call ratio of {reading[0]:.2f} "
            "(open interest is positions still held, not one day's trading)"
        ]

    return _safe("options", build)


def ownership_lines(session: Session | None, symbol: str) -> list[str]:
    """What 5% holders did in the last 90 days (new, raised, cut or ended stakes)."""

    def build() -> list[str]:
        from app.analysis.fund_signals import OWNERSHIP_HISTORY_DAYS, ownership_net_change
        from app.knowledge import FactKind, current_as_of, facts_known_as_of
        from app.knowledge.fund_holdings import ownership_filings_as_of

        if session is None:
            return []
        if not facts_known_as_of(session, FactKind.OWNERSHIP_FILING, symbol=symbol, limit=1):
            return ["5% holders (Schedule 13D/13G): none stored (never loaded)."]
        net, moves = ownership_net_change(ownership_filings_as_of(session, symbol, window_days=OWNERSHIP_HISTORY_DAYS), current_as_of())
        if not moves:
            return ["5% holders (Schedule 13D/13G): no one bought or sold in the last 90 days."]
        return [f"5% holders, last 90 days (net {net:+d}, positive is buying): " + "; ".join(moves[:4])]

    return _safe("ownership", build)


def narrative_lines(session: Session | None, symbol: str) -> tuple[list[str], bool]:
    """(lines, has_content): labelled headlines, upcoming macro releases, Fed items and posts naming the company, text
    the rules cannot score. `has_content` is True only when there is something to read beyond the standing
    statements (a labelled headline, a recent Fed item or a post), so the news analyst is not run for nothing."""

    def build() -> tuple[list[str], bool]:
        from app.analysis.fed_event_window import build_fed_window_signal
        from app.analysis.news_card_scoring import recent_cards
        from app.analysis.post_mentions import build_post_mentions_signal
        from app.knowledge import FactKind, current_as_of, facts_known_as_of

        lines: list[str] = []
        content = False
        today = date.today()
        soon = [(label, d) for label, d in ALL_MACRO_EVENTS if today <= d <= today + timedelta(days=MACRO_LOOKAHEAD_DAYS)]
        if soon:
            lines.append("Upcoming market-wide releases: " + "; ".join(f"{label} on {d.isoformat()}" for label, d in sorted(soon, key=lambda p: p[1])))
        else:
            lines.append(f"No FOMC decision, CPI release or jobs report in the next {MACRO_LOOKAHEAD_DAYS} days (hand-kept calendar).")
        if session is None:
            return lines, False
        for card in recent_cards(session, symbol)[:LABELLED_HEADLINES_SHOWN]:
            content = True
            about = "about this company" if card.get("is_about_this_company") else "not mainly about this company"
            lines.append(
                f"AI-labelled headline: {card.get('event_type')}, {card.get('sentiment')}, {card.get('materiality')} materiality, "
                f"{about}. {card.get('summary') or ''}".strip()
            )
        fed = build_fed_window_signal(session)
        lines.append(f"Fed: {fed.reason}")
        for fact in facts_known_as_of(session, FactKind.FED_SPEECH, since=current_as_of() - timedelta(days=FED_LOOKBACK_DAYS))[:FED_ITEMS_SHOWN]:
            title = (fact.payload or {}).get("title")
            if title:
                content = True
                lines.append(f"Recent Fed item ({fact.known_at.date().isoformat()}): {title}")
        posts = build_post_mentions_signal(session, symbol)
        lines.append(f"Posts (an unofficial archive): {posts.reason}")
        content = content or posts.would_score != 0
        return lines, content

    try:
        return build()
    except Exception:  # noqa: BLE001 - one source must never stop a run
        logger.debug("committee context narrative unavailable", exc_info=True)
        return [], False
