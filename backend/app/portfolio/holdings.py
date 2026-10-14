"""The portfolio view of a sleeve (PORTOFOLIO_BUILDER step 5): weights, drift, sectors, return vs SPY, drawdown.

Read-only and computed from the stored rows plus live quotes; it never writes (a GET must not touch the
equity curve). A quote that cannot be fetched falls back to the entry price, flagged in `stale_symbols`.
Random-basket comparison is study-only (scripts/studies/hold_gate_vs_spy.py), not a live number.
"""

from __future__ import annotations

import pandas as pd
from sqlmodel import Session, select

from app.data_providers.base import DataProvider
from app.data_providers.universe import get_sector
from app.portfolio.models import AccountState, EquitySnapshot, PaperPosition
from app.portfolio.sleeves import SleeveScope, scope_clause


def max_drawdown_pct(values: list[float]) -> float:
    """Largest peak-to-trough fall as a positive percent (0 for an empty or rising series)."""
    peak, worst = 0.0, 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak * 100)
    return worst


def _spy_return_since(data_provider: DataProvider, since) -> float | None:
    """SPY's return from the close on/before `since` to the latest close; None when it can't be read."""
    try:
        bars = data_provider.get_ohlcv("SPY", period="1y", interval="1d")
        dates = pd.to_datetime(bars["date"], utc=True).dt.tz_localize(None)
        before = bars[dates <= pd.Timestamp(since)]
        start = float((before if not before.empty else bars).iloc[-1 if not before.empty else 0]["close"])
        return (float(bars.iloc[-1]["close"]) / start - 1) * 100
    except Exception:  # noqa: BLE001 - the comparison is dropped, not guessed
        return None


def compute_holdings(session: Session, data_provider: DataProvider, scope: SleeveScope, target_weight_pct: float,
                     default_starting_cash: float) -> dict:
    account = session.exec(select(AccountState).where(scope_clause(AccountState.sleeve_id, scope))).first()
    starting = account.starting_cash if account else default_starting_cash
    cash = account.current_cash if account else default_starting_cash
    open_rows = session.exec(
        select(PaperPosition).where(PaperPosition.status == "open", PaperPosition.direction == "long",
                                    scope_clause(PaperPosition.sleeve_id, scope))
    ).all()

    rows, stale = [], []
    for p in open_rows:
        try:
            price = float(data_provider.get_quote(p.symbol).price)
        except Exception:  # noqa: BLE001 - show the entry price rather than hide the holding
            price = p.entry_price
            stale.append(p.symbol)
        rows.append((p, price))
    equity = cash + sum(p.shares * price for p, price in rows)

    positions = []
    sectors: dict[str, float] = {}
    for p, price in rows:
        value = p.shares * price
        weight = value / equity * 100 if equity > 0 else 0.0
        sector = get_sector(p.symbol) or "Unknown"
        sectors[sector] = sectors.get(sector, 0.0) + weight
        positions.append({
            "symbol": p.symbol, "sector": sector, "mode": p.mode, "shares": p.shares, "entry_price": p.entry_price,
            "price": price, "value": value, "weight_pct": weight, "drift_pct": weight - target_weight_pct,
            "pnl": (price - p.entry_price) * p.shares,
            "pnl_pct": (price / p.entry_price - 1) * 100 if p.entry_price else 0.0,
            "stop_loss": p.stop_loss or None, "opened_at": p.opened_at,
        })
    positions.sort(key=lambda r: -r["weight_pct"])

    spy_return = _spy_return_since(data_provider, min(p.opened_at for p in open_rows)) if open_rows else None
    total_return = (equity / starting - 1) * 100 if starting else 0.0
    curve = [s.equity_value for s in session.exec(
        select(EquitySnapshot).where(scope_clause(EquitySnapshot.sleeve_id, scope)).order_by(EquitySnapshot.timestamp)).all()]
    return {
        "target_weight_pct": target_weight_pct, "equity": equity, "cash": cash,
        "cash_pct": cash / equity * 100 if equity > 0 else 0.0, "invested": equity - cash,
        "open_pnl": sum(r["pnl"] for r in positions),
        # the same stocks held in equal amounts from their entry prices: shows whether the weights helped or hurt
        "equal_weight_return_pct": sum(r["pnl_pct"] for r in positions) / len(positions) if positions else None, "total_return_pct": total_return, "spy_return_pct": spy_return,
        "vs_spy_pct": None if spy_return is None else total_return - spy_return,
        "max_drawdown_pct": max_drawdown_pct(curve + [equity]), "positions": positions,
        "sectors": sorted(({"sector": k, "weight_pct": v} for k, v in sectors.items()), key=lambda r: -r["weight_pct"]),
        "stale_symbols": stale,
    }
