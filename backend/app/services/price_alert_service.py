"""Price alerts: user-defined alerts and automatic alerts on open paper positions.

An alert only ever sends a message and records a dated fact. It never opens, changes or
closes a position, and it never changes a stop or a target.

What this module holds:
- the condition maths as pure functions (`evaluate_condition`, `cooldown_elapsed`), so every
  rule is tested without a database or a network;
- the stored-alert operations behind the API (`create_alert`, `list_alerts`,
  `cancel_or_delete_alert`), with the validation and the cap on active alerts;
- `run_price_alert_check`, the job the scheduler runs every few minutes while markets are
  open. It reads quotes inside `fresh_data_only()`: this is a decision-grade read (an old
  price must never trigger "near your stop"), so a failed fetch means that symbol is
  skipped this tick, never evaluated on stale data.

Idea from OpenStock's price alerts (a symbol, a condition and a threshold that sends a
message when it is met); no code was copied.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.analysis.indicators import latest_atr
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.cache import fresh_data_only
from app.knowledge import FactKind, is_simulated, make_dedupe_key, record_fact
from app.markets import is_market_open_for
from app.portfolio.alert_models import NotificationLog, PriceAlert
from app.portfolio.models import PaperPosition
from app.services.notification_schedule import market_day_text
from app.services.notification_text import SendOutcome, send_text, telegram_configured
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

CONDITIONS: tuple[str, ...] = ("price_above", "price_below", "day_move_pct", "near_stop", "near_tp1")
POSITION_CONDITIONS: tuple[str, ...] = ("near_stop", "near_tp1")
UNITS: tuple[str, ...] = ("pct", "atr")

# A personal dashboard needs a handful of alerts; the cap also bounds the quotes one
# check has to fetch.
MAX_ACTIVE_ALERTS = 100
# Quotes fetched in one check (distinct symbols). Alerts on further symbols wait for the next tick.
MAX_SYMBOLS_PER_RUN = 60
DEFAULT_COOLDOWN_MINUTES = 240
MIN_COOLDOWN_MINUTES = 5
MAX_COOLDOWN_MINUTES = 7 * 24 * 60
MAX_NOTE_LENGTH = 200
# Tickers as the data providers spell them: letters, digits and . - ^ = (BRK-B, ^VIX, ES=F, BTC-USD).
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-^=]{0,14}$")
# Daily bars read for the ATR; the same 3 months the exit scan uses is plenty for a 14-day ATR.
ATR_BARS_PERIOD = "3mo"

AUTO_SOURCE = "position_alert"
# An automatic alert fires at most once per symbol-condition per US trading day: a price
# hovering at the line must not message every five minutes.
AUTO_ALERT_KEY = "position_alert:{position_id}:{condition}"


class AlertValidationError(ValueError):
    """The request cannot be saved; the message is shown to the person."""


@dataclass
class ConditionResult:
    """`met` is None when the condition cannot be judged right now (no open position, no
    ATR yet): the alert stays as it is and is looked at again on the next check."""

    met: bool | None
    value: float | None = None
    detail: str = ""


def _in_unit(distance: float, price: float, atr: float | None, unit: str) -> float | None:
    """`distance` (in price terms) as a number of `unit`: percent of the price, or ATRs."""
    if unit == "pct":
        return distance / price * 100 if price > 0 else None
    if atr is None or atr <= 0:
        return None
    return distance / atr


def evaluate_condition(
    condition: str,
    threshold: float,
    unit: str | None,
    *,
    price: float,
    change_pct: float | None = None,
    direction: str | None = None,
    stop: float | None = None,
    tp1: float | None = None,
    atr: float | None = None,
) -> ConditionResult:
    """Is this alert's condition met by the numbers given? Pure.

    - "price_above" / "price_below": the price is at or beyond `threshold`.
    - "day_move_pct": the move since the previous close is at least `threshold` percent,
      up or down (`change_pct`, as the provider reports it).
    - "near_stop" / "near_tp1": the price of an open position (`direction`, `stop`, `tp1`)
      is within `threshold` of the level, in `unit` ("pct" of the price or "atr"). A price
      already at or past the level counts as met (the distance is zero or negative).
    """
    if condition == "price_above":
        return ConditionResult(price >= threshold, price, f"price {price:,.2f} is at or above {threshold:,.2f}")
    if condition == "price_below":
        return ConditionResult(price <= threshold, price, f"price {price:,.2f} is at or below {threshold:,.2f}")
    if condition == "day_move_pct":
        if change_pct is None:
            return ConditionResult(None, None, "no day change available")
        return ConditionResult(abs(change_pct) >= threshold, change_pct, f"moved {change_pct:+.1f}% on the day (alert at {threshold:g}%)")
    if condition in POSITION_CONDITIONS:
        level = stop if condition == "near_stop" else tp1
        label = "stop" if condition == "near_stop" else "first target"
        if direction not in ("long", "short") or level is None or unit not in UNITS:
            return ConditionResult(None, None, f"no open position with a {label} to measure against")
        # Positive while the price is still on the safe/unreached side of the level.
        long_side = direction == "long"
        if condition == "near_stop":
            distance = price - level if long_side else level - price
        else:
            distance = level - price if long_side else price - level
        measured = _in_unit(distance, price, atr, unit)
        if measured is None:
            return ConditionResult(None, None, "the average daily range is not available yet")
        unit_label = "%" if unit == "pct" else " ATR"
        if distance <= 0:
            detail = f"price {price:,.2f} has reached the {label} at {level:,.2f}"
        else:
            detail = f"price {price:,.2f} is {measured:.2f}{unit_label} from the {label} at {level:,.2f}"
        return ConditionResult(measured <= threshold, measured, detail)
    return ConditionResult(None, None, f"unknown condition {condition!r}")


def cooldown_elapsed(alert: PriceAlert, now: datetime) -> bool:
    """May this alert fire at `now`? A one-shot alert fires only while it is active and
    has never fired. A repeating one fires again once its cooldown has passed."""
    if alert.status != "active":
        return False
    if alert.last_triggered_at is None:
        return True
    if not alert.repeat:
        return False
    return now >= alert.last_triggered_at + timedelta(minutes=alert.cooldown_minutes)


# ------------------------------------------------------------------ stored alerts


def normalize_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(cleaned):
        raise AlertValidationError("A symbol is 1 to 15 characters: letters, digits and . - ^ = only (for example AAPL, BRK-B).")
    return cleaned


def open_position_for(session: Session, symbol: str) -> PaperPosition | None:
    return session.exec(
        select(PaperPosition).where(PaperPosition.symbol == symbol, PaperPosition.status == "open")
    ).first()


def active_alert_count(session: Session) -> int:
    return len(session.exec(select(PriceAlert.id).where(PriceAlert.status == "active")).all())


def create_alert(
    session: Session,
    *,
    symbol: str,
    condition: str,
    threshold: float,
    unit: str | None = None,
    repeat: bool = False,
    cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
    note: str | None = None,
) -> PriceAlert:
    """Validate and save a new active alert. Raises AlertValidationError."""
    symbol = normalize_symbol(symbol)
    if condition not in CONDITIONS:
        raise AlertValidationError(f"Condition must be one of: {', '.join(CONDITIONS)}.")
    if not (threshold > 0) or threshold != threshold or threshold == float("inf"):
        raise AlertValidationError("The threshold must be a positive number.")
    if condition in POSITION_CONDITIONS:
        if unit not in UNITS:
            raise AlertValidationError("A stop or target alert needs a unit: 'pct' (percent of the price) or 'atr'.")
        if open_position_for(session, symbol) is None:
            raise AlertValidationError(f"There is no open position in {symbol}: a stop or target alert needs one.")
    else:
        unit = None
    if not MIN_COOLDOWN_MINUTES <= cooldown_minutes <= MAX_COOLDOWN_MINUTES:
        raise AlertValidationError(
            f"The cooldown must be between {MIN_COOLDOWN_MINUTES} minutes and {MAX_COOLDOWN_MINUTES // 1440} days."
        )
    if active_alert_count(session) >= MAX_ACTIVE_ALERTS:
        raise AlertValidationError(f"You already have {MAX_ACTIVE_ALERTS} active alerts: cancel some first.")
    alert = PriceAlert(
        symbol=symbol,
        condition=condition,
        threshold=float(threshold),
        unit=unit,
        repeat=repeat,
        cooldown_minutes=cooldown_minutes,
        note=(note.strip()[:MAX_NOTE_LENGTH] or None) if note else None,
    )
    session.add(alert)
    session.commit()
    session.refresh(alert)
    return alert


def list_alerts(session: Session, status: str | None = None) -> list[PriceAlert]:
    query = select(PriceAlert).order_by(PriceAlert.created_at.desc(), PriceAlert.id.desc())
    if status:
        query = query.where(PriceAlert.status == status)
    return list(session.exec(query).all())


def cancel_or_delete_alert(session: Session, alert_id: int, now: datetime | None = None) -> str | None:
    """DELETE semantics: an active alert is cancelled (kept, so the list still shows what
    was set); one that is already triggered or cancelled is removed. Returns "cancelled",
    "deleted", or None when there is no such alert."""
    alert = session.get(PriceAlert, alert_id)
    if alert is None:
        return None
    if alert.status == "active":
        alert.status = "cancelled"
        alert.cancelled_at = now if now is not None else utcnow_naive()
        session.add(alert)
        session.commit()
        return "cancelled"
    session.delete(alert)
    session.commit()
    return "deleted"


# ------------------------------------------------------------------ the check


Notifier = Callable[[AppSettings, str], SendOutcome]


@dataclass
class PriceAlertRunResult:
    symbols_checked: int = 0
    fired: list[str] = field(default_factory=list)
    skipped_symbols: list[str] = field(default_factory=list)  # market closed, or no fresh quote
    messages_sent: int = 0


@dataclass
class _Market:
    price: float
    change_pct: float | None
    atr: float | None = None


def format_alert_message(symbol: str, headline: str, detail: str, note: str | None = None) -> str:
    lines = [f"Price alert - {symbol}", headline, detail]
    if note:
        lines.append(f"Your note: {note}")
    lines.append("Paper trading only: nothing was traded or changed.")
    return "\n".join(lines)


_CONDITION_HEADLINES = {
    "price_above": "Price above your level",
    "price_below": "Price below your level",
    "day_move_pct": "Big move today",
    "near_stop": "Close to your stop",
    "near_tp1": "Close to your first target",
}


def _record_alert_fact(
    session: Session,
    *,
    symbol: str,
    key_parts: tuple,
    now: datetime,
    payload: dict,
) -> None:
    try:
        record_fact(
            session,
            kind=FactKind.PRICE_ALERT,
            source="price_alerts",
            symbol=symbol,
            dedupe_key=make_dedupe_key("price_alert", *key_parts),
            known_at=now,
            payload=payload,
        )
    except Exception:  # noqa: BLE001 - the record must never stop the message
        logger.exception("Could not record the price alert fact for %s", symbol)
        session.rollback()


def _fetch_market(symbol: str, provider: DataProvider, need_atr: bool) -> _Market | None:
    """Quote (and, when needed, the 14-day ATR) for one symbol; None when the fresh
    fetch failed. Always called inside fresh_data_only()."""
    try:
        quote = provider.get_quote(symbol)
    except Exception as exc:  # noqa: BLE001 - one symbol failing must not stop the rest
        logger.info("Price alerts: no fresh quote for %s: %s", symbol, type(exc).__name__)
        return None
    if not quote.price or quote.price <= 0:
        return None
    atr: float | None = None
    if need_atr:
        try:
            atr = latest_atr(provider.get_ohlcv(symbol, period=ATR_BARS_PERIOD, interval="1d"))
        except Exception as exc:  # noqa: BLE001 - ATR alerts just wait; price alerts still run
            logger.info("Price alerts: no daily bars for %s: %s", symbol, type(exc).__name__)
    return _Market(price=float(quote.price), change_pct=quote.change_pct_24h, atr=atr)


def run_price_alert_check(
    session: Session,
    settings: AppSettings,
    data_provider: DataProvider,
    now: datetime | None = None,
    *,
    notifier: Notifier = send_text,
) -> PriceAlertRunResult:
    """Evaluate every active alert (and, when enabled, every open position) against fresh
    quotes; for each condition that is met, record a fact and send one Telegram message.
    Never raises. Symbols whose market is closed are skipped (crypto is always open)."""
    result = PriceAlertRunResult()
    now = now if now is not None else utcnow_naive()
    if is_simulated():
        return result  # a backtest is replaying history: quotes are live
    try:
        alerts = [a for a in list_alerts(session, "active") if cooldown_elapsed(a, now)]
        positions = list(session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all())
        position_by_symbol = {p.symbol: p for p in positions}
        auto_on = settings.price_alert_positions_enabled and bool(positions)
        if not alerts and not auto_on:
            return result

        # Which symbols need a look, oldest alert first, then held positions.
        wanted: list[str] = []
        for symbol in [a.symbol for a in reversed(alerts)] + ([p.symbol for p in positions] if auto_on else []):
            if symbol not in wanted:
                wanted.append(symbol)
        atr_needed = {a.symbol for a in alerts if a.condition in POSITION_CONDITIONS and a.unit == "atr"}
        if auto_on:
            atr_needed |= set(position_by_symbol)  # the automatic stop alert measures in ATRs

        markets: dict[str, _Market] = {}
        with fresh_data_only():
            for symbol in wanted[:MAX_SYMBOLS_PER_RUN]:
                if not is_market_open_for(symbol, now):
                    result.skipped_symbols.append(symbol)
                    continue
                market = _fetch_market(symbol, data_provider, symbol in atr_needed)
                if market is None:
                    result.skipped_symbols.append(symbol)
                    continue
                markets[symbol] = market
        result.symbols_checked = len(markets)

        for alert in alerts:
            market = markets.get(alert.symbol)
            if market is None:
                continue
            position = position_by_symbol.get(alert.symbol) if alert.condition in POSITION_CONDITIONS else None
            outcome = evaluate_condition(
                alert.condition,
                alert.threshold,
                alert.unit,
                price=market.price,
                change_pct=market.change_pct,
                direction=position.direction if position else None,
                stop=position.stop_loss if position else None,
                tp1=position.tp1 if position else None,
                atr=market.atr,
            )
            if outcome.met:
                _fire_user_alert(session, settings, alert, market, outcome, now, notifier, result)

        if auto_on:
            _check_positions(session, settings, positions, markets, now, notifier, result)
    except Exception:  # noqa: BLE001 - the scheduler must never see an exception from here
        logger.exception("Price alert check failed")
        session.rollback()
    return result


def _send(settings: AppSettings, text: str, notifier: Notifier, result: PriceAlertRunResult) -> None:
    if not telegram_configured(settings):
        return  # nothing to send to: the event is still recorded
    outcome = notifier(settings, text)
    if outcome.ok:
        result.messages_sent += 1


def _fire_user_alert(
    session: Session,
    settings: AppSettings,
    alert: PriceAlert,
    market: _Market,
    outcome: ConditionResult,
    now: datetime,
    notifier: Notifier,
    result: PriceAlertRunResult,
) -> None:
    # State first, message after: if the send fails the alert has still fired once and
    # a one-shot alert is not retried forever.
    alert.trigger_count += 1
    alert.last_triggered_at = now
    alert.triggered_at = alert.triggered_at or now
    alert.last_value = outcome.value
    if not alert.repeat:
        alert.status = "triggered"
    session.add(alert)
    session.commit()
    result.fired.append(f"{alert.symbol}:{alert.condition}")
    _record_alert_fact(
        session,
        symbol=alert.symbol,
        key_parts=("user", alert.id, alert.trigger_count),
        now=now,
        payload={
            "source": "user",
            "alert_id": alert.id,
            "condition": alert.condition,
            "threshold": alert.threshold,
            "unit": alert.unit,
            "value": outcome.value,
            "price": market.price,
            "detail": outcome.detail,
        },
    )
    _send(
        settings,
        format_alert_message(alert.symbol, _CONDITION_HEADLINES[alert.condition], outcome.detail, alert.note),
        notifier,
        result,
    )


def _check_positions(
    session: Session,
    settings: AppSettings,
    positions: list[PaperPosition],
    markets: dict[str, _Market],
    now: datetime,
    notifier: Notifier,
    result: PriceAlertRunResult,
) -> None:
    """The automatic alerts on open positions: near the stop (within price_alert_stop_atr
    ATRs) and near the first target (within price_alert_tp1_pct percent), each at most once
    per US trading day per position."""
    today = market_day_text(now)
    checks = (
        ("near_stop", settings.price_alert_stop_atr, "atr"),
        ("near_tp1", settings.price_alert_tp1_pct, "pct"),
    )
    for position in positions:
        market = markets.get(position.symbol)
        if market is None:
            continue
        for condition, threshold, unit in checks:
            key = AUTO_ALERT_KEY.format(position_id=position.id, condition=condition)
            log = session.get(NotificationLog, key)
            if log is not None and log.last_day == today:
                continue
            outcome = evaluate_condition(
                condition,
                threshold,
                unit,
                price=market.price,
                direction=position.direction,
                stop=position.stop_loss,
                tp1=position.tp1,
                atr=market.atr,
            )
            if not outcome.met:
                continue
            log = log or NotificationLog(key=key)
            log.last_day = today
            log.last_sent_at = now
            session.add(log)
            session.commit()
            result.fired.append(f"{position.symbol}:{condition}")
            _record_alert_fact(
                session,
                symbol=position.symbol,
                key_parts=(AUTO_SOURCE, position.id, condition, today),
                now=now,
                payload={
                    "source": AUTO_SOURCE,
                    "position_id": position.id,
                    "condition": condition,
                    "threshold": threshold,
                    "unit": unit,
                    "value": outcome.value,
                    "price": market.price,
                    "detail": outcome.detail,
                },
            )
            _send(
                settings,
                format_alert_message(
                    position.symbol,
                    _CONDITION_HEADLINES[condition] + f" ({position.direction} position)",
                    outcome.detail,
                ),
                notifier,
                result,
            )
