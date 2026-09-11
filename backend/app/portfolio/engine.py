from __future__ import annotations

import math

from sqlmodel import Session, select

from app.data_providers.base import AllProvidersFailedError, DataProvider
from app.portfolio.models import AccountState, EquitySnapshot, PaperPosition, TradePlanRecord
from app.timeutil import utcnow_naive


class InsufficientCashError(Exception):
    """Raised when the account can't afford even 1 share. Opening anyway
    would create a phantom zero-share position — it clears status checks and
    shows up in the positions list, but represents no real exposure, so it
    silently masquerades as an executed trade that never actually happened."""


class DuplicatePositionError(Exception):
    """Raised when a symbol already has an open position. The v1 engine's
    exit rule (mark_to_market closes a position fully, all-or-nothing — see
    notes/Decisions.md) assumes one position per symbol at a time; silently
    allowing a second would pyramid into it with no way to distinguish which
    fill a later stop/TP hit belongs to."""


class MaxPositionsExceededError(Exception):
    """Raised when opening would push the account past
    settings.max_concurrent_positions. Enforced here, in the engine itself,
    so every path that can open a position — manual open, a trade plan's
    auto-execute, and the unattended auto-scan loop — respects the same cap;
    previously only the auto-scan loop (automation_service.py) checked it,
    so manually generating (with auto-execute on) or directly opening
    positions could blow straight through the configured limit."""


class PaperTradingEngine:
    def __init__(
        self,
        session: Session,
        data_provider: DataProvider,
        starting_cash: float = 100_000.0,
        max_concurrent_positions: int | None = None,
    ):
        self._session = session
        self._data_provider = data_provider
        self._starting_cash = starting_cash
        self._max_concurrent_positions = max_concurrent_positions

    def get_account_state(self) -> AccountState:
        account = self._session.exec(select(AccountState)).first()
        if account is None:
            account = AccountState(starting_cash=self._starting_cash, current_cash=self._starting_cash)
            self._session.add(account)
            self._session.commit()
            self._session.refresh(account)
        return account

    def open_position(self, trade_plan: TradePlanRecord) -> PaperPosition:
        account = self.get_account_state()

        existing = self._session.exec(
            select(PaperPosition).where(PaperPosition.symbol == trade_plan.symbol, PaperPosition.status == "open")
        ).first()
        if existing is not None:
            raise DuplicatePositionError(f"{trade_plan.symbol} already has an open position (id={existing.id})")

        if self._max_concurrent_positions is not None:
            open_count = len(self._session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all())
            if open_count >= self._max_concurrent_positions:
                raise MaxPositionsExceededError(
                    f"Already at the {self._max_concurrent_positions}-position cap "
                    "(Settings -> Max Concurrent Positions) — close a position before opening another."
                )

        shares = trade_plan.suggested_shares

        # Same cash cap for both directions: a short's sale proceeds are
        # credited to current_cash below (real brokers do the same), but
        # they're collateral against the short, not free money — treating
        # raw current_cash as spendable for the NEXT position would let a
        # chain of shorts each inflate the cash available to the next trade,
        # producing aggregate exposure with no real bound relative to actual
        # capital. _available_cash() nets out collateral already reserved
        # for open shorts so this cap only ever grants buying power the
        # account actually has.
        available = self._available_cash(account)
        required_cash = shares * trade_plan.entry
        if required_cash > available:
            shares = math.floor(available / trade_plan.entry) if trade_plan.entry > 0 else 0

        if shares <= 0:
            raise InsufficientCashError(
                f"Account can't afford 1 share of {trade_plan.symbol} at ${trade_plan.entry:.2f} "
                f"(available cash: ${available:.2f}). Increase paper starting cash or "
                "risk % in Settings."
            )

        if trade_plan.direction == "long":
            account.current_cash -= shares * trade_plan.entry
        else:
            account.current_cash += shares * trade_plan.entry

        position = PaperPosition(
            trade_plan_id=trade_plan.id,
            symbol=trade_plan.symbol,
            direction=trade_plan.direction,
            entry_price=trade_plan.entry,
            stop_loss=trade_plan.stop,
            tp1=trade_plan.tp1,
            tp2=trade_plan.tp2,
            shares=shares,
        )
        trade_plan.status = "executed"

        self._session.add(position)
        self._session.add(account)
        self._session.add(trade_plan)
        self._session.commit()
        self._session.refresh(position)
        self._record_equity_snapshot()
        self._session.refresh(position)  # _record_equity_snapshot()'s commit expires attributes
        return position

    def _available_cash(self, account: AccountState) -> float:
        """current_cash minus collateral reserved for currently open shorts
        — see the comment in open_position() for why raw current_cash isn't
        the right number to size a new position against."""
        open_shorts = self._session.exec(
            select(PaperPosition).where(PaperPosition.status == "open", PaperPosition.direction == "short")
        ).all()
        reserved = sum(p.shares * p.entry_price for p in open_shorts)
        return account.current_cash - reserved

    def close_position(self, position: PaperPosition, close_price: float, reason: str) -> PaperPosition:
        account = self.get_account_state()
        risk_per_share = abs(position.entry_price - position.stop_loss)
        sign = 1 if position.direction == "long" else -1
        realized_pnl = (close_price - position.entry_price) * position.shares * sign
        realized_r = realized_pnl / (risk_per_share * position.shares) if risk_per_share > 0 and position.shares > 0 else 0.0

        if position.direction == "long":
            account.current_cash += position.shares * close_price
        else:
            account.current_cash -= position.shares * close_price

        position.status = "closed"
        position.closed_at = utcnow_naive()
        position.close_price = close_price
        position.close_reason = reason
        position.realized_pnl = realized_pnl
        position.realized_r = realized_r

        self._session.add(position)
        self._session.add(account)
        self._session.commit()
        self._session.refresh(position)
        self._record_equity_snapshot()
        self._session.refresh(position)  # _record_equity_snapshot()'s commit expires attributes
        return position

    def mark_to_market(self) -> list[PaperPosition]:
        """Checks each open position's latest bar high/low against stop/TP1.
        v1 exit rule: whichever of stop or TP1 hits first closes the full
        position; TP2 is informational only (see notes/Decisions.md)."""
        closed: list[PaperPosition] = []
        open_positions = self._session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all()

        for position in open_positions:
            try:
                bars = self._data_provider.get_ohlcv(position.symbol, period="5d", interval="1d")
            except AllProvidersFailedError:
                continue
            if bars.empty:
                continue
            latest = bars.iloc[-1]
            high, low = float(latest["high"]), float(latest["low"])

            if position.direction == "long":
                if low <= position.stop_loss:
                    closed.append(self.close_position(position, position.stop_loss, "stop_hit"))
                elif high >= position.tp1:
                    closed.append(self.close_position(position, position.tp1, "tp1_hit"))
            else:
                if high >= position.stop_loss:
                    closed.append(self.close_position(position, position.stop_loss, "stop_hit"))
                elif low <= position.tp1:
                    closed.append(self.close_position(position, position.tp1, "tp1_hit"))

        self._record_equity_snapshot()
        for position in closed:
            self._session.refresh(position)
        return closed

    def _record_equity_snapshot(self) -> None:
        account = self.get_account_state()
        open_positions = self._session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all()

        mark_value = 0.0
        for position in open_positions:
            try:
                quote = self._data_provider.get_quote(position.symbol)
                price = quote.price
            except AllProvidersFailedError:
                price = position.entry_price
            # Mirrors close_position()'s cash math exactly: a long ADDS
            # shares*price to cash when closed, a short SUBTRACTS it (you
            # pay to buy back and cover) — this is "cash if every open
            # position were closed right now," not a raw notional sum.
            mark_value += position.shares * price if position.direction == "long" else -(position.shares * price)

        snapshot = EquitySnapshot(equity_value=account.current_cash + mark_value, cash_balance=account.current_cash)
        self._session.add(snapshot)
        self._session.commit()
