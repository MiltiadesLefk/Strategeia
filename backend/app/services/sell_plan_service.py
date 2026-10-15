"""Sell plan and rebuy watch (PORTOFOLIO_BUILDER.md F4). Rules only: the AI decides nothing here.

Each open hold position is read daily on fresh data and counted for bad signals:
quality gate fails, N closes under the 200-day, thesis broken, clustered insider/Congress sells, and
(supporting only) clearly negative news. 0 signals: nothing. 1: alert, listed on the position
(`sell_signals`), and a position in profit gets its stop raised. 2 or more: the whole position is sold.
Fund (13F) sells are not used: the stored data is per-fund quarterly changes, no per-stock sell feed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd
from sqlmodel import Session, select

from app.analysis.fundamental_scoring import score_news_sentiment
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.cache import fresh_data_only
from app.knowledge.congress_trades import congress_trades_as_of
from app.knowledge.insider_trades import CLUSTER_MIN_INSIDERS, OPEN_MARKET_SELL, insider_trades_as_of
from app.knowledge.point_in_time import is_simulated
from app.portfolio.models import PaperPosition, RebuyWatch, Sleeve
from app.portfolio.thesis_models import ThesisRecord
from app.services.hold_buy_service import MA_DAYS, check_quality, hold_buy
from app.services.sleeve_service import build_sleeve_engine
from app.services.telegram_service import notify
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

SELL_WINDOW_DAYS = 30  # insider / Congress sells looked at
NEGATIVE_NEWS_SCORE = -2  # score_news_sentiment at or below this is "clearly negative"
SOLD_REASONS = ("disaster_stop", "sell_plan")


def days_under_ma(closes: list[float], days: int) -> bool:
    """The last `days` closes were each under their own 200-day average."""
    if len(closes) < MA_DAYS + days - 1:
        return False
    return all(closes[-i] < sum(closes[-i - MA_DAYS + 1 : len(closes) - i + 1]) / MA_DAYS for i in range(1, days + 1))


def total_signals(core: int, negative_news: bool) -> int:
    """Negative news only ever counts as the second signal, never alone."""
    return core + (1 if negative_news and core >= 1 else 0)


def trail_stop(entry: float, price: float, high: float, trail_pct: float, current_stop: float) -> float | None:
    """The raised stop for a position in profit (breakeven, or `trail_pct` under the high), or None."""
    if price <= entry:
        return None
    new = max(entry, high * (1 - trail_pct / 100)) if trail_pct > 0 else entry
    return new if current_stop < new < price else None


def evaluate_signals(
    session: Session, data_provider: DataProvider, position: PaperPosition, settings: AppSettings
) -> tuple[list[str], float, float]:
    """(signal names, last price, highest close since entry). Fresh data only."""
    symbol = position.symbol
    with fresh_data_only():
        bars = data_provider.get_ohlcv(symbol, period="1y", interval="1d")
        closes = [float(c) for c in bars["close"]]
        price = float(data_provider.get_quote(symbol).price)
        dates = pd.to_datetime(bars["date"], utc=True, errors="coerce").dt.tz_localize(None)
        held_closes = bars["close"][dates >= pd.Timestamp(position.opened_at.date())]
        high = max([*map(float, held_closes), price])
        core: list[str] = []
        if not check_quality(data_provider, symbol).passed:
            core.append("quality_gate")
        if days_under_ma(closes, settings.sell_ma200_days):
            core.append("below_200day")
        thesis = session.exec(select(ThesisRecord).where(ThesisRecord.position_id == position.id)).first()
        if thesis is not None and thesis.thesis_broken:
            core.append("thesis_broken")
        sells = insider_trades_as_of(session, symbol, None, SELL_WINDOW_DAYS, codes=(OPEN_MARKET_SELL,), include_10b5_1=False)
        insiders = {t.owner_key or t.owner_name for t in sells}
        members = {t.member_key for t in congress_trades_as_of(session, symbol, window_days=SELL_WINDOW_DAYS, side="sell")}
        if len(insiders) >= CLUSTER_MIN_INSIDERS or len(members) >= CLUSTER_MIN_INSIDERS:
            core.append("clustered_sells")
        try:
            news_bad = score_news_sentiment(data_provider.get_news(symbol), "long")[0] <= NEGATIVE_NEWS_SCORE
        except Exception:  # noqa: BLE001 - news is supporting only; no news is no signal
            news_bad = False
    names = core + (["negative_news"] if total_signals(len(core), news_bad) > len(core) else [])
    return names, price, high


def run_sell_plan(settings: AppSettings, data_provider: DataProvider, session: Session) -> dict[str, list[str]]:
    """The daily job: read every open hold position, act, then update and use the rebuy watch."""
    out: dict[str, list[str]] = {"sold": [], "flagged": [], "rebought": []}
    if is_simulated():
        return out
    engines: dict[int | None, object] = {}

    def engine_for(sleeve_id: int | None):
        if sleeve_id not in engines:
            sleeve = session.get(Sleeve, sleeve_id) if sleeve_id else None
            engines[sleeve_id] = build_sleeve_engine(session, data_provider, settings, sleeve, hold=True)
        return engines[sleeve_id]

    def tell(text: str) -> None:
        notify(settings.telegram_bot_token, settings.telegram_chat_id, text)

    for position in session.exec(select(PaperPosition).where(PaperPosition.status == "open", PaperPosition.mode == "hold")).all():
        try:
            names, price, high = evaluate_signals(session, data_provider, position, settings)
            if len(names) >= 2:
                engine_for(position.sleeve_id).close_position_by_hand(position, price, "sell_plan")
                out["sold"].append(position.symbol)
                tell(f"Sell plan sold {position.symbol}: {', '.join(names)}.")
                continue
            flagged = ",".join(names) or None
            new_stop = trail_stop(position.entry_price, price, high, settings.sell_trail_pct, position.stop_loss) if names else None
            if flagged != position.sell_signals or new_stop:
                if names and flagged != position.sell_signals:
                    out["flagged"].append(position.symbol)
                    tell(f"Sell plan: {position.symbol} has a bad signal ({flagged}). Review it."
                         + (f" Stop raised to {new_stop:,.2f}." if new_stop else ""))
                position.sell_signals = flagged
                if new_stop:
                    position.stop_loss, position.stop_raised_at = new_stop, utcnow_naive()
                session.add(position)
                session.commit()
        except Exception:  # noqa: BLE001 - one position's failure (data, closed market) never stops the rest
            logger.exception("Sell plan failed for %s", position.symbol)
            session.rollback()

    _update_rebuy_watch(session)
    held = {p.symbol for p in session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all()}
    cutoff = utcnow_naive() - timedelta(days=settings.rebuy_cooldown_days)
    for watch in session.exec(select(RebuyWatch).where(RebuyWatch.status == "watching", RebuyWatch.sold_at <= cutoff)).all():
        if watch.symbol in held:
            continue
        try:
            gate = check_quality(data_provider, watch.symbol, require_trend=False)
            if not (gate.passed and gate.dip):
                continue
            sold = session.get(PaperPosition, watch.sold_position_id)
            bought = hold_buy(session, engine_for(sold.sleeve_id if sold else None), data_provider, settings, watch.symbol,
                              reason=f"rebuy of sale #{watch.sold_position_id}", sleeve_id=sold.sleeve_id if sold else None)
            watch.status, watch.rebuy_position_id = "rebought", bought.id
            session.add(watch)
            session.commit()
            out["rebought"].append(watch.symbol)
            tell(f"Rebought {watch.symbol} (sale #{watch.sold_position_id}): quality gate passes and it is in a dip.")
        except Exception:  # noqa: BLE001 - closed market, no cash, caps: try again next day
            logger.info("Rebuy of %s not done", watch.symbol, exc_info=True)
            session.rollback()
    return out


def run_hold_reviews(settings: AppSettings, data_provider: DataProvider, session: Session) -> list[str]:
    """O10: a dated message per open hold position every `hold_review_days`. Never sells or moves a stop."""
    sent: list[str] = []
    if is_simulated() or settings.hold_review_days <= 0:
        return sent
    now = utcnow_naive()
    for position in session.exec(select(PaperPosition).where(PaperPosition.status == "open", PaperPosition.mode == "hold")).all():
        since = position.last_reviewed_at or position.opened_at
        if now - since < timedelta(days=settings.hold_review_days):
            continue
        try:
            gate = check_quality(data_provider, position.symbol)
            with fresh_data_only():
                price = float(data_provider.get_quote(position.symbol).price)
        except Exception:  # noqa: BLE001 - no fresh read: skip, unstamped, retried tomorrow
            logger.info("Review of %s skipped", position.symbol, exc_info=True)
            continue
        pnl = (price / position.entry_price - 1) * 100
        notify(settings.telegram_bot_token, settings.telegram_chat_id,
               f"Review {position.symbol}: held {(now - position.opened_at).days} days, {pnl:+.1f}%. "
               f"Passes: {', '.join(c.name for c in gate.checks if c.ok) or 'none'}. "
               f"Fails: {', '.join(c.name for c in gate.checks if not c.ok) or 'none'}. "
               f"Sell signals: {position.sell_signals or 'none'}.")
        position.last_reviewed_at = now
        session.add(position)
        session.commit()
        sent.append(position.symbol)
    return sent


def _update_rebuy_watch(session: Session) -> None:
    """A rebuy stopped or sold again leaves the watch for good; a new stop/sale joins it."""
    closed = session.exec(
        select(PaperPosition).where(PaperPosition.status == "closed", PaperPosition.mode == "hold",
                                    PaperPosition.close_reason.in_(SOLD_REASONS))
    ).all()
    for pos in closed:
        again = session.exec(select(RebuyWatch).where(RebuyWatch.rebuy_position_id == pos.id)).first()
        if again is not None:
            again.status = "dropped"
            session.add(again)
        elif session.exec(select(RebuyWatch).where(RebuyWatch.sold_position_id == pos.id)).first() is None:
            session.add(RebuyWatch(symbol=pos.symbol, sold_position_id=pos.id, sold_at=pos.closed_at or utcnow_naive(),
                                   sold_price=pos.close_price or 0.0, sold_reason=pos.close_reason or ""))
    session.commit()
