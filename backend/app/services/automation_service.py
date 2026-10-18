from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session, select

from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.universe import get_default_watchlist
from app.llm_providers.base import LLMProvider
from app.portfolio.engine import Clock, resolve_clock
from app.portfolio.models import DeferredEvaluation, PaperPosition
from app.services.deferred_evaluation_service import PENDING, due_redos, redo_window_open
from app.services import smart_money_pipeline_service
from app.services.telegram_service import notify

logger = logging.getLogger(__name__)


def hold_scan_symbol(
    settings: AppSettings, data_provider: DataProvider, session: Session, engine, symbol: str, reason: str,
    *, price_only: bool = False, skip_gate: bool = False, evidence=None, rate: bool = False,
    run_id: str | None = None,
) -> bool:
    """One symbol of the hold scan: gate, dip rule, hold buy. True = bought. Shared by the live scan and the
    backtest (`price_only` = the trend check alone, the only part of the gate knowable at a past date;
    `rate` = the universe lane's pass rule is the 32-point evidence rating (services/rating_service) instead of the quality gate (live only);
    `skip_gate` = no gate and no dip rule, only the random-entry baseline passes it; `evidence` = a live-only
    EvidenceBonus: negative means not bought, otherwise it is named in the buy reason).
    The engine's refusals are logged and mean "not bought"; MarketClosedError and anything unexpected propagate."""
    from app.portfolio.engine import (
        DuplicatePositionError, InsufficientCashError, MaxPositionsExceededError, SectorConcentrationError,
        SleeveDisabledError, StalePlanError,
    )
    from app.services.hold_buy_service import check_quality, hold_buy

    rating_row = None
    try:
        dip = False
        if not skip_gate:
            if rate:
                from app.services.rating_service import rate_stock, record_rating

                gate = rate_stock(data_provider, symbol, settings.min_confidence_for_trade, session)
                rating_row = record_rating(session, run_id, "scan", symbol, "quality", gate) if run_id else None
            else:
                gate = check_quality(data_provider, symbol, price_only=price_only, session=session, settings=settings)
            if not gate.passed or (settings.hold_buy_only_dips and not gate.dip):
                return False
            dip = gate.dip
            if rate:
                reason = reason.replace("passed the quality gate", f"passed the evidence rating ({gate.checks[0].note})")
        if evidence is not None:
            if evidence.points < 0:
                logger.info("Hold scan: %s not bought: negative evidence (%s)", symbol, evidence.describe())
                return False
            if evidence.points:
                reason += f" ({evidence.describe()})"
        hold_buy(session, engine, data_provider, settings, symbol, reason=reason + (" and is in a dip" if dip else ""))
        if rating_row is not None:
            rating_row.bought = True
            session.add(rating_row)
            session.commit()
        return True
    except (InsufficientCashError, DuplicatePositionError, MaxPositionsExceededError, SectorConcentrationError,
            SleeveDisabledError, StalePlanError) as exc:
        logger.info("Hold scan: %s not bought: %s", symbol, exc)
        return False


def run_hold_scan(settings: AppSettings, data_provider: DataProvider, session: Session) -> list[str]:
    """The daily portfolio-builder scan: every watchlist stock not yet held goes through the quality
    gate and a pass is hold-bought (target weight, wide stop), up to `hold_max_new_per_scan`. A stock
    that fails is simply not bought. Returns the symbols bought. The engine's checks still apply."""
    from app.portfolio.engine import (
        InsufficientCashError, MarketClosedError, SectorConcentrationError, SleeveDisabledError, StalePlanError,
    )
    from app.services.hold_buy_service import check_quality
    from app.services.sleeve_service import build_sleeve_engine

    if not settings.auto_execute_trade_plans:
        return []  # buying on its own is exactly what that switch turns off
    held = {p.symbol for p in session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all()}
    run_id = uuid.uuid4().hex  # groups this scan's ratings for the Ratings page
    engine = build_sleeve_engine(session, data_provider, settings, hold=True)
    bought: list[str] = []
    candidates = {s: "hold scan [quality lane]: passed the quality gate" for s in get_default_watchlist(settings.scan_universe_size) if s not in held}
    if settings.news_discovery_enabled:
        from app.services.news_discovery_service import discover_from_news

        for symbol, headline in discover_from_news(settings, data_provider).items():
            if symbol not in held and symbol not in candidates:
                candidates[symbol] = f'hold scan [quality lane] (news discovery, headline: "{headline}"): passed the quality gate'
    evidence: dict = {}
    if settings.evidence_bonus_enabled:
        from app.services.evidence_service import evidence_for

        evidence = {s: evidence_for(settings, data_provider, session, s) for s in candidates}
        candidates = dict(sorted(candidates.items(), key=lambda kv: -evidence[kv[0]].points))  # stable: ties keep list order
    market_closed = False
    for symbol, reason in candidates.items():
        if len(bought) >= settings.hold_max_new_per_scan:
            break
        try:
            if hold_scan_symbol(settings, data_provider, session, engine, symbol, reason, evidence=evidence.get(symbol), rate=True, run_id=run_id):
                bought.append(symbol)
        except MarketClosedError:
            logger.info("Hold scan stopped: the market is closed")
            market_closed = True
            break
        except Exception:
            logger.exception("Hold scan: failed to evaluate %s", symbol)
    if settings.big_news_buy_enabled and not market_closed:
        from app.services.big_news_service import big_news_scan_symbol, market_8k_symbols, wire_news_headlines
        from app.services.news_discovery_service import discover_from_news

        # Big-news lane, whole market: the watchlist, tickers named in watched stocks' headlines, and companies with a
        # big stored 8-K from the SEC market-wide feed. Same per-scan cap; found stocks are analysed even if not bought.
        extras: dict[str, str | None] = {s: None for s in candidates}
        try:
            for ticker, headline in discover_from_news(settings, data_provider).items():
                extras.setdefault(ticker, headline)
            for ticker in market_8k_symbols(session, settings):
                extras.setdefault(ticker, None)
            for ticker, headline in wire_news_headlines(settings).items():
                if extras.get(ticker) is None:
                    extras[ticker] = headline
        except Exception:
            logger.exception("Big-news lane: could not gather market-wide candidates")
        for symbol, headline in extras.items():
            if len(bought) >= settings.hold_max_new_per_scan:
                break
            if symbol in bought or symbol in held:
                continue
            try:
                if big_news_scan_symbol(settings, data_provider, session, engine, symbol, headline):
                    bought.append(symbol)
            except MarketClosedError:
                logger.info("Big-news lane stopped: the market is closed")
                break
            except Exception:
                logger.exception("Big-news lane: failed to evaluate %s", symbol)
    if settings.hold_add_on_dips:
        from app.services.hold_buy_service import hold_add

        # A half-size (failed-gate) position is topped up to a full slice here only because the gate passes now.
        for position in session.exec(select(PaperPosition).where(
                PaperPosition.status == "open", PaperPosition.direction == "long", PaperPosition.mode == "hold")).all():
            if not engine._owns(position):
                continue
            try:
                gate = check_quality(data_provider, position.symbol, session=session)
                if gate.passed and gate.dip:
                    hold_add(session, engine, data_provider, settings, position)
                    notify(settings.telegram_bot_token, settings.telegram_chat_id, f"Hold scan added to {position.symbol} (dip, passed the quality gate).")
            except MarketClosedError:
                logger.info("Hold scan dip adds stopped: the market is closed")
                break
            except (InsufficientCashError, SectorConcentrationError, SleeveDisabledError, StalePlanError, ValueError) as exc:
                logger.info("Hold scan: %s not added to: %s", position.symbol, exc)
            except Exception:
                logger.exception("Hold scan: failed to add to %s", position.symbol)
    if bought:
        notify(settings.telegram_bot_token, settings.telegram_chat_id, f"Hold scan bought {', '.join(bought)} (quality gate or big-news lane).")
    return bought


# ----------------------------------------------------------- market-open redo


@dataclass
class RedoOutcome:
    """What one pass of the market-open redo did, symbol by symbol."""

    skipped: list[str] = field(default_factory=list)
    # Smart-money candidates (an evaluation or a queued review Buy) handed to the smart-money pipeline.
    smart_money: list[str] = field(default_factory=list)

    @property
    def handled(self) -> int:
        return len(self.skipped) + len(self.smart_money)


def _resolve(deferral: DeferredEvaluation, status: str, outcome: str, now: datetime) -> None:
    deferral.status = status
    deferral.outcome = outcome
    deferral.resolved_at = now


def run_market_open_redos(
    settings: AppSettings,
    data_provider: DataProvider,
    llm_provider: LLMProvider,
    session: Session,
    *,
    clock: Clock | None = None,
) -> RedoOutcome:
    """Every smart-money evaluation deferred because the market was closed is redone from fresh data once the
    market has been open for REDO_DELAY_AFTER_OPEN (09:45 ET on a normal day). The scheduler calls this every
    15 minutes through the session, so a redo missed while the app was down is picked up on a later tick.
    A queued row that is not a smart-money candidate (a swing-plan redo from before swing trading was retired)
    is closed as skipped: nothing evaluates a swing plan any more."""
    clock = resolve_clock(clock)
    now = clock()
    outcome = RedoOutcome()

    for deferral in due_redos(session, now):
        symbol = deferral.symbol
        if not redo_window_open(symbol, now):
            continue  # a holiday, before 09:45, or after the close: wait for the next tick

        if deferral.candidate_id is None:
            _resolve(deferral, "skipped", "Swing plans are retired; nothing redoes this.", now)
            session.add(deferral)
            session.commit()
            outcome.skipped.append(symbol)
            continue
        # A smart-money candidate waiting for the open (its evaluation, or a Buy queued from the review
        # queue): the pipeline redoes it from fresh data with its own routing and caps.
        line = smart_money_pipeline_service.handle_redo(session, settings, data_provider, llm_provider, deferral, clock=clock)
        logger.info("Smart-money redo for %s: %s", symbol, line)
        session.refresh(deferral)
        if deferral.status != PENDING:
            outcome.smart_money.append(symbol)

    if outcome.handled:
        parts = [
            f"{label}: {', '.join(symbols)}"
            for label, symbols in (("skipped", outcome.skipped), ("smart money", outcome.smart_money))
            if symbols
        ]
        notify(settings.telegram_bot_token, settings.telegram_chat_id, "Market-open redo — " + "; ".join(parts) + ".")
    return outcome
