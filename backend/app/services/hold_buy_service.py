"""The hold buy (PORTOFOLIO_BUILDER.md step 2): one stock bought for the portfolio at a target weight.

No swing logic: no entry/stop derivation from chart levels, no targets, no risk-based size. Entry is
the live quote; the stop is the wider of `hold_stop_pct` below it and the 200-day average (0 = no
automatic stop, alert only); size is `hold_target_weight_pct` of the account. Every engine check
(session, duplicate, caps, stale re-quote, cash, ADV) still applies in `open_position`.
"""

from __future__ import annotations

import math

from sqlmodel import Session, select

from app.analysis.quality_gate import GateResult, evaluate_quality_gate
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.cache import fresh_data_only
from app.portfolio.engine import InsufficientCashError, PaperTradingEngine
from app.portfolio.models import PaperPosition, TradePlanRecord

MA_DAYS = 200


def hold_stop_level(entry: float, ma200: float | None, stop_pct: float) -> float:
    """Wider (lower) of entry minus `stop_pct` and the 200-day average; 0.0 = alert-only."""
    if stop_pct <= 0:
        return 0.0
    pct_stop = entry * (1 - stop_pct / 100)
    return min(pct_stop, ma200) if ma200 else pct_stop


def hold_shares(equity: float, weight_pct: float, price: float) -> int:
    return math.floor(equity * weight_pct / 100 / price) if price > 0 else 0


def account_value(session: Session, engine: PaperTradingEngine) -> float:
    """Cash plus open longs at cost (cost basis, not marked: close enough to size a 4.5% slice)."""
    held = session.exec(select(PaperPosition).where(PaperPosition.status == "open", PaperPosition.direction == "long")).all()
    return engine.get_account_state().current_cash + sum(p.shares * p.entry_price for p in held if engine._owns(p))


def hold_buy(
    session: Session,
    engine: PaperTradingEngine,
    data_provider: DataProvider,
    settings: AppSettings,
    symbol: str,
    *,
    reason: str = "hold buy",
    sleeve_id: int | None = None,
    size_pct: float = 100.0,
) -> PaperPosition:
    """Buy `symbol` in hold mode (`size_pct` of the normal slice). Raises the engine's errors (market closed, duplicate, no cash...)."""
    with fresh_data_only():
        price = float(data_provider.get_quote(symbol).price)
        try:
            closes = data_provider.get_ohlcv(symbol, period="1y", interval="1d")["close"]
            ma200 = float(closes.tail(MA_DAYS).mean()) if len(closes) >= MA_DAYS else None
        except Exception:  # noqa: BLE001 - no 200-day average just means the percentage stop alone
            ma200 = None
    shares = hold_shares(account_value(session, engine), settings.hold_target_weight_pct * size_pct / 100, price)
    if shares <= 0:
        raise InsufficientCashError(f"A {settings.hold_target_weight_pct * size_pct / 100:g}% slice of the account can't buy 1 share of {symbol} at ${price:.2f}.")
    plan = TradePlanRecord(
        symbol=symbol, direction="long", entry=price, stop=hold_stop_level(price, ma200, settings.hold_stop_pct),
        tp1=0.0, tp2=0.0, suggested_shares=shares, confidence_score=0, status="pending",
        reason=reason, time_horizon="hold", sleeve_id=sleeve_id,
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return engine.open_position(plan, mode="hold")


def hold_add(
    session: Session, engine: PaperTradingEngine, data_provider: DataProvider, settings: AppSettings, position: PaperPosition
) -> PaperPosition:
    """Top a held stock back up toward the target weight (one more slice of what is missing, never above it)."""
    with fresh_data_only():
        price = float(data_provider.get_quote(position.symbol).price)
    target = hold_shares(account_value(session, engine), settings.hold_target_weight_pct, price)
    missing = target - position.shares
    if missing <= 0:
        raise InsufficientCashError(f"{position.symbol} is already at or above its {settings.hold_target_weight_pct:g}% target weight.")
    return engine.add_to_hold_position(position, missing)


def check_quality(data_provider: DataProvider, symbol: str) -> GateResult:
    """Fetch what the gate reads (fresh data only) and run it. A failed fetch is a failed check."""
    with fresh_data_only():
        price = float(data_provider.get_quote(symbol).price)
        closes = [float(c) for c in data_provider.get_ohlcv(symbol, period="1y", interval="1d")["close"]]
        try:
            overview = data_provider.get_company_overview(symbol)
        except Exception:  # noqa: BLE001 - missing data fails the check instead of guessing
            overview = None
        try:
            years = data_provider.get_financials(symbol).years
        except Exception:  # noqa: BLE001
            years = []
    return evaluate_quality_gate(price, closes, overview, years)
