"""The 32-point evidence rating (restored from c7dd16a~1 / e3822c3~1, long side only, no AI): the universe lane's pass rule.

Every part is plain rules from `app/analysis/*` (technical, fundamentals, news keywords, weekly/SPY confirmation, options,
insiders, earnings track record, plus the Smart Money / filings / valuation / price parts in `live_evidence`). A part with
no data scores 0, never a guess; VIX, expected move and macro days can only subtract. The stock passes when its points
reach `AppSettings.min_confidence_for_trade`, a PERCENT of `MAX_SCORE_FOR_CONFIDENCE` (default 16% of 32 = 5 points). The result is a `GateResult`
so `hold_scan_symbol` treats it like the quality gate (and keeps the price-only dip flag)."""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.analysis.confidence import (
    ATR_PERIOD, MAX_SCORE_FOR_CONFIDENCE, TECHNICAL_SCORE_CAP, _confidence_score, clamp_points,
)
from app.analysis.earnings_history_scoring import score_earnings_surprise_track_record
from app.analysis.expected_move import score_expected_move
from app.analysis.fundamental_scoring import score_fundamentals, score_news_sentiment
from app.analysis.indicators import latest_atr
from app.analysis.insider_scoring import insider_sell_split, score_insider_activity
from app.analysis.live_evidence import EXTRA_LABELS, evaluate_extra_evidence
from app.analysis.macro_calendar import score_macro_event_proximity
from app.analysis.market_confirmation import (
    MARKET_PROXY_SYMBOL, VIX_PROXY_SYMBOL, score_market_confirmation, score_vix_regime,
)
from app.analysis.options_scoring import score_options_positioning
from app.analysis.quality_gate import GateCheck, GateResult, is_dip
from app.analysis.scanner_scoring import score_symbol
from app.analysis.trend import analyze_chart
from app.data_providers.base import DataProvider
from app.data_providers.cache import fresh_data_only
from app.knowledge.point_in_time import is_simulated

logger = logging.getLogger(__name__)
DIRECTION = "long"  # hold mode only buys


def _try(read, default=None):
    """Optional evidence must never fail a rating: a failed read is None and that part scores 0."""
    try:
        return read()
    except Exception:  # noqa: BLE001
        logger.debug("rating input unavailable", exc_info=True)
        return default


def rate_stock(data_provider: DataProvider, symbol: str, bar_pct: int, session: Session | None = None) -> GateResult:
    """Rate `symbol` for a long hold. A failed quote or price history raises (like the gate: no data = no buy)."""
    with fresh_data_only():
        quote = data_provider.get_quote(symbol)
        ohlcv = data_provider.get_ohlcv(symbol, period="1y", interval="1d")
        chart = analyze_chart(ohlcv)
        volume_ratio = quote.volume / quote.avg_volume_20d if quote.avg_volume_20d > 0 else 1.0
        technical = score_symbol(symbol, quote.price, quote.change_pct_24h, chart, volume_ratio)
        overview = _try(lambda: data_provider.get_company_overview(symbol))
        years = _try(lambda: data_provider.get_financials(symbol).years, [])
        news = _try(lambda: data_provider.get_news(symbol, limit=5), [])
        earnings_date = _try(lambda: data_provider.get_earnings_date(symbol))
        weekly = _try(lambda: analyze_chart(data_provider.get_ohlcv(symbol, period="2y", interval="1wk")))
        market_ohlcv = None if symbol == MARKET_PROXY_SYMBOL else _try(
            lambda: data_provider.get_ohlcv(MARKET_PROXY_SYMBOL, period="1y", interval="1d"))
        market_chart = _try(lambda: analyze_chart(market_ohlcv)) if market_ohlcv is not None else None
        vix = None if symbol == VIX_PROXY_SYMBOL else _try(
            lambda: float(data_provider.get_ohlcv(VIX_PROXY_SYMBOL, period="1mo", interval="1d")["close"].iloc[-1]))
        options = _try(lambda: data_provider.get_options_summary(symbol))
        insiders = _try(lambda: data_provider.get_insider_activity(symbol))
        history = _try(lambda: data_provider.get_earnings_history(symbol), [])
        extra_inputs = {"market_ohlcv": market_ohlcv, "options_chain": _try(lambda: data_provider.get_options_chain(symbol)),
                        "estimate": _try(lambda: data_provider.get_earnings_estimate(symbol)), "valuation": None}
        from app.services.valuation_service import get_valuation
        extra_inputs["valuation"] = _try(lambda: get_valuation(data_provider, symbol))

    price = float(quote.price)
    atr = latest_atr(ohlcv, ATR_PERIOD)
    atr_pct = (atr / chart.price * 100) if atr and chart.price else None
    parts: dict[str, int] = {"technical": min(TECHNICAL_SCORE_CAP, technical.score)}
    reasons: dict[str, list[str]] = {}

    def add(key: str, scored: tuple[int, list[str]]) -> None:
        parts[key], reasons[key] = scored

    add("fundamentals", score_fundamentals(overview, years, earnings_date, price, DIRECTION) if overview else (0, []))
    add("news", score_news_sentiment(news, DIRECTION))
    add("confluence", score_market_confirmation(DIRECTION, weekly, market_chart))
    add("vix", score_vix_regime(vix))
    add("options", score_options_positioning(DIRECTION, options))
    add("insider", score_insider_activity(DIRECTION, insiders, _try(lambda: insider_sell_split(session, symbol))))
    add("expected_move", score_expected_move(DIRECTION, options, atr_pct))
    add("earnings_track", score_earnings_surprise_track_record(DIRECTION, history))
    add("macro_event", (0, []) if is_simulated() else score_macro_event_proximity())
    extra = evaluate_extra_evidence(DIRECTION, session, symbol, overview, years, ohlcv=ohlcv,
                                    earnings_history=history, **extra_inputs)
    parts.update(extra.points)
    reasons["extra"] = extra.reasons

    total = clamp_points(sum(parts.values()))
    labels = {**EXTRA_LABELS, "technical": "technical", "fundamentals": "fundamentals", "news": "news", "confluence": "weekly/market",
              "vix": "VIX", "options": "options", "insider": "insiders", "expected_move": "expected move",
              "earnings_track": "earnings record", "macro_event": "macro days"}
    shown = ", ".join(f"{labels.get(k, k)} {v:+d}" for k, v in parts.items() if v)
    percent = _confidence_score(total)
    passed = percent >= bar_pct
    note = f"{total}/{MAX_SCORE_FOR_CONFIDENCE} points = {percent}% (bar {bar_pct}%)" + (f": {shown}" if shown else "")
    closes = [float(c) for c in ohlcv["close"]]
    return GateResult(passed, [GateCheck("rating", passed, note)], dip=is_dip(closes))
