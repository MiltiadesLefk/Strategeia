"""The 32-point evidence rating (restored from c7dd16a~1 / e3822c3~1, long side only, no AI): the universe lane's pass rule.

Every part is plain rules from `app/analysis/*` (technical, fundamentals, news keywords, weekly/SPY confirmation, options,
insiders, earnings track record, plus the Smart Money / filings / valuation / price parts in `live_evidence`). A part with
no data scores 0, never a guess; VIX, expected move and macro days can only subtract. The stock passes when its points
reach `AppSettings.min_confidence_for_trade`, a PERCENT of `MAX_SCORE_FOR_CONFIDENCE` (default 16% of 32 = 5 points). The result is a `GateResult`
so `hold_scan_symbol` treats it like the quality gate (and keeps the price-only dip flag)."""

from __future__ import annotations

import json
import logging

from sqlmodel import Session, select

from app.analysis.confidence import (
    ATR_PERIOD, MAX_SCORE_FOR_CONFIDENCE, TECHNICAL_SCORE_CAP, _confidence_score, clamp_points,
)
from app.analysis.earnings_history_scoring import score_earnings_surprise_track_record
from app.analysis.expected_move import score_expected_move
from app.analysis.fundamental_scoring import score_fundamentals, score_news_sentiment
from app.analysis.indicators import latest_atr
from app.analysis.insider_scoring import insider_sell_split, score_insider_activity
from app.analysis.live_evidence import EXTRA_LABELS, evaluate_extra_evidence
from app.analysis.news_card_scoring import ai_news_score
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
from app.portfolio.models import RatingRecord

logger = logging.getLogger(__name__)
DIRECTION = "long"  # hold mode only buys
AI_NEWS_RATING_CAP = 3  # most points the AI-labelled news can add or remove


def _try(read, default=None):
    """Optional evidence must never fail a rating: a failed read is None and that part scores 0."""
    try:
        return read()
    except Exception:  # noqa: BLE001
        logger.debug("rating input unavailable", exc_info=True)
        return default


def _news_items(session: Session | None, symbol: str, news: list, ai_available: bool) -> list[dict]:
    """The headlines the news part looked at, for the expandable row: the AI's labelled cards, else the raw headlines."""
    if ai_available and session is not None:
        from app.analysis.news_card_scoring import recent_cards

        return [{"text": c.get("headline", ""), "meta": " · ".join(filter(None, [
                    c.get("publisher"), (c.get("article") or c).get("materiality")])),
                 "sentiment": (c.get("article") or c).get("sentiment"), "url": c.get("url", "")} for c in _try(lambda: recent_cards(session, symbol), [])]
    return [{"text": n.headline, "meta": " · ".join(filter(None, [n.source, str(n.published_at)[:10]])), "url": n.url} for n in news or []]


def _money(v) -> str:
    return f"${v:,.0f}" if v else "n/a"


def _smart_money_items(session: Session | None, symbol: str) -> dict[str, list[dict]]:
    """Who bought and sold: the insider, Congress and fund records behind those parts, for the expandable rows."""
    if session is None:
        return {}
    from app.knowledge.congress_trades import congress_trades_as_of
    from app.knowledge.fund_holdings import fund_holders_of_symbol
    from app.knowledge.insider_trades import OPEN_MARKET_BUY, OPEN_MARKET_SELL, insider_trades_as_of

    out: dict[str, list[dict]] = {}
    trades = _try(lambda: insider_trades_as_of(session, symbol, codes=(OPEN_MARKET_BUY, OPEN_MARKET_SELL)), [])
    out["insiders"] = [{"text": f"{'BOUGHT' if t.code == OPEN_MARKET_BUY else 'SOLD'} {_money(t.value)} — {t.owner_name or 'insider'}"
                               f"{' (' + t.officer_title + ')' if t.officer_title else ''}{' under a 10b5-1 plan' if t.is_10b5_1 else ''}",
                        "meta": str(t.transaction_date or t.known_at.date()), "url": t.filing_url or ""} for t in trades[:25]]
    cong = _try(lambda: congress_trades_as_of(session, symbol), [])
    out["Congress"] = [{"text": f"{t.side.upper()} {t.amount_text} — {t.member}", "meta": f"traded {t.trade_date}, filed {t.filed_date}",
                        "url": t.filing_url or ""} for t in cong[:25]]
    funds = _try(lambda: fund_holders_of_symbol(session, symbol), [])
    out["Funds (13F)"] = [{"text": f"{h.manager or h.cik}: {h.status.replace('_', ' ')} ({_money(h.value)}"
                                  f"{'' if h.change_pct is None else f', {h.change_pct:+.0f}%'})",
                           "meta": f"quarter {h.period}", "url": h.filing_url or ""} for h in funds[:25]]
    return out


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
    # AI-labelled news (headline label, article read when it was positive/negative) when any exists, else the keyword scorer.
    ai_available, ai_points, ai_reasons = ai_news_score(session, symbol, DIRECTION, AI_NEWS_RATING_CAP)
    add("news", (ai_points, ai_reasons) if ai_available else score_news_sentiment(news, DIRECTION))
    items = {"news": _news_items(session, symbol, news, ai_available), **_smart_money_items(session, symbol)}
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
    why = {k: "; ".join(v) for k, v in reasons.items() if k != "extra" and v}
    why.update({k: n for k, n in extra.notes.items() if n})
    if len(closes) >= 200:
        ma200 = sum(closes[-200:]) / 200
        why["technical"] = "; ".join(filter(None, [why.get("technical"), f"price {price:,.2f} is {(price / ma200 - 1) * 100:+.1f}% vs its 200-day average {ma200:,.2f}"]))
    detail = {"points": total, "max": MAX_SCORE_FOR_CONFIDENCE, "percent": percent, "bar_pct": bar_pct,
              "parts": {labels.get(k, k): v for k, v in parts.items()},
              "why": {labels.get(k, k): w for k, w in why.items()},
              "items": {labels.get(k, k): v for k, v in items.items() if v}}
    return GateResult(passed, [GateCheck("rating", passed, note)], dip=is_dip(closes), detail=detail)


def record_rating(session: Session, run_id: str, source: str, symbol: str, lane: str, gate: GateResult | None, note: str | None = None) -> RatingRecord | None:
    """Save one rating for the history page. Best-effort: a database problem never changes a buy decision.
    `gate` None = a stock saved without a rating (zero score); `note` overrides the gate's note."""
    d = (gate.detail if gate else None) or {}
    try:
        row = RatingRecord(run_id=run_id, source=source, symbol=symbol, lane=lane, points=d.get("points", 0),
                           max_points=d.get("max", MAX_SCORE_FOR_CONFIDENCE), percent=d.get("percent", 0), passed=bool(gate and gate.passed),
                           dip=bool(gate and gate.dip),
                           note=note if note is not None else (gate.checks[0].note if gate and gate.checks else ""), detail_json=json.dumps(d))
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    except Exception:  # noqa: BLE001
        logger.warning("could not save the rating of %s", symbol, exc_info=True)
        session.rollback()
        return None


def record_market_news(session: Session, run_id: str, source: str, found: dict[str, str]) -> None:
    """The market-wide wire headlines the news reader labelled (tickers outside the watchlist), with the AI's labels."""
    from app.analysis.news_card_scoring import recent_cards

    from app.services.big_news_service import WIRE_URLS

    for ticker, headline in (found or {}).items():
        cards = _try(lambda: recent_cards(session, ticker), [])
        # a headline stored by an earlier scan has no url; this scan's wire fetch knows it
        items = [{"text": c.get("headline", ""), "meta": " · ".join(filter(None, [c.get("event_type"), c.get("materiality")])),
                  "sentiment": c.get("sentiment"), "url": c.get("url") or WIRE_URLS.get(c.get("headline", ""), "")} for c in cards] \
            or [{"text": headline, "meta": "wire", "url": WIRE_URLS.get(headline, "")}]
        record_report_item(session, run_id, source, ticker, "market news", f"Wire headline read: {headline}", items)


def rate_market_finds(settings, data_provider: DataProvider, session: Session, run_id: str, source: str, found: dict[str, str]) -> list[str]:
    """Rate each market-wide ticker with a positive (bullish) headline as if it were in the universe; one that
    reaches the bar joins the universe. Returns the tickers added. A ticker that cannot be rated is skipped."""
    from app.analysis.news_card_scoring import recent_cards
    from app.services.market_find_service import LANE_BIG_NEWS, adopt_found_stock

    added: list[str] = []
    for ticker, headline in (found or {}).items():
        if not any(c.get("sentiment") == "positive" for c in _try(lambda: recent_cards(session, ticker), [])):
            continue
        try:
            gate = rate_stock(data_provider, ticker, settings.min_confidence_for_trade, session)
        except Exception:  # noqa: BLE001 - no data for one ticker must not stop the run
            logger.info("Market news: %s could not be rated", ticker, exc_info=True)
            continue
        record_rating(session, run_id, source, ticker, LANE_BIG_NEWS, gate, note=f"Bullish wire headline ({headline}): {gate.checks[0].note}")
        if gate.passed and adopt_found_stock(session, settings, data_provider, ticker, LANE_BIG_NEWS, f'headline: "{headline}"', gate):
            added.append(ticker)
    return added


REPORT_LANES = ("market news", "smart money trades")  # daily-report rows about stocks outside the universe; not ratings


def record_report_item(session: Session, run_id: str, source: str, symbol: str, lane: str, note: str, items: list[dict]) -> None:
    """One market-wide thing the scan analysed (a wire headline, a smart-money trade), listed under the day's report."""
    record_rating(session, run_id, source, symbol, lane, None, note=note)
    row = session.exec(select(RatingRecord).where(RatingRecord.run_id == run_id, RatingRecord.symbol == symbol,
                                                   RatingRecord.lane == lane).order_by(RatingRecord.id.desc())).first()
    if row is not None:
        row.detail_json = json.dumps({"items": {lane: items}})
        session.add(row)
        session.commit()


def rate_universe(settings, data_provider: DataProvider, session: Session, run_id: str, source: str = "manual", llm_provider=None) -> int:
    """Rate every watchlist stock and save the results; buys nothing. Returns how many were rated.
    With an AI provider the news reader labels the headlines first, exactly as the morning scan does."""
    from app.data_providers.universe import get_default_watchlist

    from app.services.smart_money_adopt_service import adopt_smart_money_finds

    done = 0
    watchlist = get_default_watchlist(settings.scan_universe_size)
    if llm_provider is not None:
        from app.services.news_reader_service import read_news

        found = _try(lambda: read_news(settings, data_provider, llm_provider, session, list(watchlist), market=True), {})
        record_market_news(session, run_id, source, found)
        rate_market_finds(settings, data_provider, session, run_id, source, found)
    _try(lambda: adopt_smart_money_finds(settings, data_provider, session, run_id=run_id, rate=True))
    for symbol in watchlist:
        try:
            gate = rate_stock(data_provider, symbol, settings.min_confidence_for_trade, session)
        except Exception:  # noqa: BLE001 - one stock with no data must not stop the run
            logger.info("Rating run: %s could not be rated", symbol, exc_info=True)
            continue
        record_rating(session, run_id, source, symbol, "quality", gate)
        done += 1
    return done
