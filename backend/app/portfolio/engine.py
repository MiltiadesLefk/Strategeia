from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from sqlmodel import Session, select

from app.data_providers.base import AllProvidersFailedError, DataProvider
from app.data_providers.cache import fresh_data_only
from app.data_providers.universe import get_sector
from app.markets import (
    format_market_time,
    is_daily_bar_final,
    is_market_open_for,
    next_us_open,
    us_closure_reason,
)
from app.portfolio.excursion import LastBar, compute_excursion
from app.portfolio.kill_switch_models import active_pause
from app.portfolio.liquidity_slippage import impact_bps
from app.portfolio.models import AccountState, EquitySnapshot, PaperPosition, Sleeve, TradePlanRecord
from app.portfolio.sleeves import (
    SleeveScope,
    ensure_core_sleeve,
    find_core_sleeve,
    scope_clause,
)
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

RESOLUTION_DAILY = "daily"  # PaperPosition.exit_resolution of an exit found on a daily bar

# Returns "now" as a naive-UTC datetime, the convention for every stored
# timestamp (timeutil.utcnow_naive).
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class ExitFound:
    """One exit the scan found: the fill, why, how it was placed in time, and
    the bars the position lived through (for the best/worst-price record)."""

    fill_price: float
    reason: str  # "disaster_stop"
    resolution: str  # PaperPosition.exit_resolution
    # Daily bars up to and including the exit bar.
    bars_walked: int
    # Total slippage in bps the market fill paid (flat + liquidity); None when the
    # liquidity model is off or the exit was a limit fill.
    slippage_bps: float | None = None


def default_clock() -> datetime:
    """The engine's notion of "now" when no clock is passed in: the real time.

    Everything time-dependent in the engine reads its clock — the market-session
    gate in open_position, and the opened_at / closed_at / equity-snapshot
    timestamps — so a backtest can drive it through simulated time by passing
    `clock=` instead. A module-level function looked up when an engine is
    built (see resolve_clock), never bound as a default argument, so the test
    suite can pin it to a known open session (tests/conftest.py)."""
    return utcnow_naive()


def resolve_clock(clock: Clock | None) -> Clock:
    """`clock`, or whatever default_clock is at call time. Callers outside
    this module use this rather than importing default_clock by name, which
    would bind the original and ignore a pinned one."""
    return clock if clock is not None else default_clock

# How much history mark_to_market() pulls when looking for a stop/TP touch.
# Must comfortably exceed the longest realistic gap between two runs (an app
# left off over a holiday weekend, a laptop shut for a week) — the engine
# walks every bar since entry, so anything shorter silently drops exits that
# happened in the gap. See _bars_after_entry.
EXIT_SCAN_PERIOD = "3mo"

# A plan's entry price is the close of the bar it was generated from. Execute
# it days later and that number is fiction. Past this drift the plan is stale
# and must be regenerated rather than filled at a price the market left behind.
MAX_ENTRY_DRIFT_PCT = 0.02


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


class SectorConcentrationError(Exception):
    """Raised when opening would put more than settings.max_positions_per_sector
    positions into one sector. max_concurrent_positions alone bounds the
    COUNT of open risk, not its independence: five 1%-risk positions read as
    "5% at risk" on the dashboard, but five semis on the same tape is one
    correlated 5% bet a single sector drawdown takes out together."""


class StalePlanError(Exception):
    """Raised when the market has moved more than MAX_ENTRY_DRIFT_PCT away
    from the price the plan was built on. The whole plan — stop distance,
    share count, R:R — is derived from that entry; filling at a materially
    different price silently changes every one of those numbers while the
    UI still shows the original ones."""


class MarketClosedError(Exception):
    """Raised when the symbol's market is closed at the engine's `now`. The
    only price available then is the previous session's close, and a paper
    fill at it is a trade nobody could actually have made: on Sunday
    2026-09-27 two auto-executed plans opened NVDA and AAPL at Friday's close
    plus slippage (plan.md F-6). Checked here, in the engine, like
    MaxPositionsExceededError, so every path that opens a position — a plan's
    auto-execute, the manual Execute button, the auto-scan loop, and later
    the watchers and the backtester — shares one rule. Crypto (`-USD`) never
    closes, so it can always open."""


class SleeveDisabledError(Exception):
    """Raised when a position is opened in a sleeve that was switched off. A
    disabled sleeve takes no new trades, but its open positions are still
    managed (marked, stopped out, closed) until they are gone."""


class SleevePausedError(SleeveDisabledError):
    """Raised when a kill switch has paused the sleeve. A subclass of the disabled
    error so every caller that already handles that one handles this too."""


class PaperTradingEngine:
    """The paper account of ONE sleeve (the core sleeve unless `sleeve` is given).

    Every read and write below is scoped to that sleeve: its cash, its open
    positions, the duplicate-symbol rule, the position and sector caps, the
    equity snapshots and the exit scan. Two sleeves may hold the same symbol at
    the same time, and the caps apply to each sleeve on its own. An engine built
    without a sleeve is exactly the single-account engine the app always had
    (rows with no sleeve_id belong to core), which is what backtests, replays
    and the missed-trade study rely on."""

    def __init__(
        self,
        session: Session,
        data_provider: DataProvider,
        starting_cash: float = 100_000.0,
        max_concurrent_positions: int | None = None,
        slippage_bps: float = 0.0,
        commission_per_trade: float = 0.0,
        max_positions_per_sector: int | None = None,
        max_position_pct_of_adv: float | None = None,
        clock: Clock | None = None,
        sleeve: Sleeve | None = None,
        liquidity_slippage_coefficient: float | None = None,
        exit_scan_period: str = EXIT_SCAN_PERIOD,
    ):
        self._session = session
        # Only plain values are kept (not the ORM object, whose attributes expire
        # on every commit). None = core, resolved lazily: see _scope().
        self._sleeve_id = sleeve.id if sleeve is not None and sleeve.key != "core" else None
        self._is_core = self._sleeve_id is None
        self._data_provider = data_provider
        self._starting_cash = starting_cash
        self._max_concurrent_positions = max_concurrent_positions
        self._slippage_bps = slippage_bps
        # None / 0 = off: slippage is the flat slippage_bps only (see liquidity_slippage.py).
        self._liquidity_coefficient = (
            liquidity_slippage_coefficient if liquidity_slippage_coefficient and liquidity_slippage_coefficient > 0 else None
        )
        self._commission_per_trade = commission_per_trade
        self._max_positions_per_sector = max_positions_per_sector
        self._max_position_pct_of_adv = max_position_pct_of_adv
        self._clock = resolve_clock(clock)
        # How much daily history the exit scan reads. The core's 3 months suit
        # a position held a few weeks; a sleeve that holds longer (smart money) passes a longer one.
        self._exit_scan_period = exit_scan_period

    def now(self) -> datetime:
        """The engine's current moment (naive UTC): real time, or a simulated
        one when a clock was passed in."""
        return self._clock()

    def is_market_open_for(self, symbol: str) -> bool:
        """Whether open_position would accept `symbol` right now, by the
        engine's own clock — lets a caller decide what to do with a plan
        (the market-open redo defers it) before attempting the fill."""
        return is_market_open_for(symbol, self.now())

    # --------------------------------------------------------------- sleeve

    def _scope(self) -> SleeveScope:
        """Which rows this engine covers. For core it is looked up each time
        (cheap) because the core row may not exist yet, and a read must not
        create it; NULL-sleeve rows are core's either way."""
        if not self._is_core:
            return SleeveScope(self._sleeve_id, False)
        core = find_core_sleeve(self._session)
        return SleeveScope(core.id if core else None, True)

    def _sleeve_row(self) -> Sleeve:
        """The sleeve this engine writes to (core is created on first use)."""
        if self._is_core:
            return ensure_core_sleeve(self._session)
        sleeve = self._session.get(Sleeve, self._sleeve_id)
        if sleeve is None:
            raise ValueError(f"Sleeve {self._sleeve_id} no longer exists")
        return sleeve

    def _in_sleeve(self, column):
        return scope_clause(column, self._scope())

    def _open_positions(self) -> list[PaperPosition]:
        return list(
            self._session.exec(
                select(PaperPosition).where(PaperPosition.status == "open", self._in_sleeve(PaperPosition.sleeve_id))
            ).all()
        )

    def get_account_state(self) -> AccountState:
        account = self._session.exec(select(AccountState).where(self._in_sleeve(AccountState.sleeve_id))).first()
        if account is None:
            sleeve_id = self._sleeve_row().id
            account = AccountState(
                starting_cash=self._starting_cash, current_cash=self._starting_cash, sleeve_id=sleeve_id
            )
            self._session.add(account)
            self._session.commit()
            self._session.refresh(account)
        return account

    # ----------------------------------------------------------------- fills

    def _market_bps(self, symbol: str, shares: float) -> float:
        """Total slippage in bps for a market fill of `shares`: the flat
        slippage_bps plus, when the liquidity model is on, the size-vs-ADV extra."""
        extra = 0.0
        if self._liquidity_coefficient is not None:
            try:
                adv = self._data_provider.get_quote(symbol).avg_volume_20d
            except AllProvidersFailedError:
                adv = None
            extra = impact_bps(shares, adv, self._liquidity_coefficient)
        return max(self._slippage_bps, 0.0) + extra

    def _exit_bps(self, position: PaperPosition, *, market: bool = True, shares: float | None = None) -> float | None:
        """The bps to record for an exit; None when the model is off or the fill was a limit.
        `shares` is the size being sold when that is not the whole position (a scaled-out runner)."""
        if self._liquidity_coefficient is None or not market:
            return None
        return self._market_bps(position.symbol, position.shares if shares is None else shares)

    def _slip(self, price: float, *, buying: bool, symbol: str | None = None, shares: float | None = None) -> float:
        """Market-order fills cross the spread and move with the book; this
        nudges the fill against the account by slippage_bps. Applied to
        entries and to stop exits (both market orders) — never to a
        take-profit, which is a resting limit order that fills at its price
        or better by construction."""
        bps = self._slippage_bps
        if symbol is not None and shares is not None and self._liquidity_coefficient is not None:
            bps = self._market_bps(symbol, shares)
        if bps <= 0:
            return price
        factor = bps / 10_000.0
        return price * (1 + factor) if buying else price * (1 - factor)

    @staticmethod
    def _exit_fill_price(direction: str, bar_open: float, level: float, *, is_stop: bool) -> float:
        """Models the gap. A stop is a market order *triggered* by touching
        the level, not a fill *at* the level: if the bar opens beyond the
        stop, the fill happens at the open, which is materially worse — and
        is exactly the case a naive engine flatters away. A take-profit is a
        resting limit, so a bar that opens past it fills at the open, which
        is better. Both directions of that asymmetry are modelled here."""
        if direction == "long":
            # stop sits below: a gap-down opens under it -> fill at the open.
            # tp sits above: a gap-up opens over it -> fill at the open.
            return min(bar_open, level) if is_stop else max(bar_open, level)
        # short: stop sits above, tp sits below — mirror image.
        return max(bar_open, level) if is_stop else min(bar_open, level)

    # ------------------------------------------------------------ open/close

    def open_position(self, trade_plan: TradePlanRecord, *, mode: str = "swing") -> PaperPosition:
        # First, before any other check or the re-quote: nothing else about
        # the fill means anything if no real trade could happen right now.
        now = self.now()
        if not is_market_open_for(trade_plan.symbol, now):
            raise MarketClosedError(
                f"The US market is closed ({us_closure_reason(now)}), so {trade_plan.symbol} can't be "
                f"filled at a real price — the only quote is the last session's close. It reopens "
                f"{format_market_time(next_us_open(now))}."
            )

        sleeve = self._sleeve_row()
        if not sleeve.enabled:
            raise SleeveDisabledError(
                f"The '{sleeve.name}' sleeve is disabled, so it opens no new positions (its open ones are still managed)."
            )
        sleeve_id = sleeve.id
        pause = active_pause(self._session, sleeve.key)
        if pause is not None:
            raise SleevePausedError(
                f"The '{sleeve.name}' sleeve is paused ({pause.reason}: {pause.detail}), so it opens no new "
                "positions (its open ones are still managed). Resume it from the Sleeves page."
            )

        account = self.get_account_state()

        # Per sleeve: two sleeves may each hold the same symbol (they are separate
        # accounts); within one the one-position-per-symbol rule is unchanged.
        existing = self._session.exec(
            select(PaperPosition).where(
                PaperPosition.symbol == trade_plan.symbol,
                PaperPosition.status == "open",
                self._in_sleeve(PaperPosition.sleeve_id),
            )
        ).first()
        if existing is not None:
            raise DuplicatePositionError(f"{trade_plan.symbol} already has an open position (id={existing.id})")

        open_positions = self._open_positions()

        if self._max_concurrent_positions is not None and len(open_positions) >= self._max_concurrent_positions:
            raise MaxPositionsExceededError(
                f"Already at the {self._max_concurrent_positions}-position cap "
                "(Settings -> Max Concurrent Positions) — close a position before opening another."
            )

        self._check_sector_concentration(trade_plan.symbol, open_positions)

        planned_entry = trade_plan.entry
        entry_price = self._resolve_entry_price(trade_plan.symbol, planned_entry, trade_plan.direction)

        shares = trade_plan.suggested_shares or 0

        # Same cash cap for both directions: a short's sale proceeds are
        # credited to current_cash below (real brokers do the same), but
        # they're collateral against the short, not free money — treating
        # raw current_cash as spendable for the NEXT position would let a
        # chain of shorts each inflate the cash available to the next trade,
        # producing aggregate exposure with no real bound relative to actual
        # capital. _available_cash() nets out collateral already reserved
        # for open shorts so this cap only ever grants buying power the
        # account actually has.
        available = self._available_cash(account) - self._commission_per_trade
        required_cash = shares * entry_price
        if required_cash > available:
            shares = math.floor(available / entry_price) if entry_price > 0 else 0

        shares = self._cap_by_liquidity(trade_plan.symbol, shares)

        entry_slippage_bps = None
        if self._liquidity_coefficient is not None and shares > 0:
            # Size is known only now, so the size-dependent part is added to the
            # fill here. A higher fill price can break the cash check: re-size.
            flat = max(self._slippage_bps, 0.0)
            extra = self._market_bps(trade_plan.symbol, shares) - flat
            entry_slippage_bps = flat + extra
            factor = extra / 10_000.0
            entry_price = entry_price * (1 + factor) if trade_plan.direction == "long" else entry_price * (1 - factor)
            if shares * entry_price > available and entry_price > 0:
                shares = math.floor(available / entry_price)

        if shares <= 0:
            raise InsufficientCashError(
                f"Account can't afford 1 share of {trade_plan.symbol} at ${entry_price:.2f} "
                f"(available cash: ${available:.2f}). Increase paper starting cash or "
                "risk % in Settings."
            )

        if trade_plan.direction == "long":
            account.current_cash -= shares * entry_price
        else:
            account.current_cash += shares * entry_price
        account.current_cash -= self._commission_per_trade

        position = PaperPosition(
            trade_plan_id=trade_plan.id,
            symbol=trade_plan.symbol,
            direction=trade_plan.direction,
            entry_price=entry_price,
            planned_entry_price=planned_entry,
            stop_loss=trade_plan.stop,
            tp1=trade_plan.tp1,
            tp2=trade_plan.tp2,
            shares=shares,
            fees_paid=self._commission_per_trade,
            opened_at=now,
            sleeve_id=sleeve_id,
            entry_slippage_bps=entry_slippage_bps,
            mode=mode,
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

    def add_to_hold_position(self, position: PaperPosition, shares: int) -> PaperPosition:
        """Buy more of an open hold position: one row stays, its entry price becomes the average cost.
        The stop level is unchanged. Same session, sleeve, stale-quote, cash and ADV checks as a first buy."""
        if position.mode != "hold" or position.status != "open" or position.direction != "long":
            raise ValueError("Only an open long hold position can be added to.")
        if not self._owns(position):
            raise ValueError(f"Position {position.id} belongs to another sleeve; add through that sleeve's engine.")
        now = self.now()
        if not is_market_open_for(position.symbol, now):
            raise MarketClosedError(
                f"The US market is closed ({us_closure_reason(now)}), so {position.symbol} can't be bought at a real price. "
                f"It reopens {format_market_time(next_us_open(now))}."
            )
        sleeve = self._sleeve_row()
        if not sleeve.enabled:
            raise SleeveDisabledError(f"The '{sleeve.name}' sleeve is disabled, so it opens no new positions.")
        with fresh_data_only():
            quote = float(self._data_provider.get_quote(position.symbol).price)
        price = self._resolve_entry_price(position.symbol, quote, "long")
        account = self.get_account_state()
        available = self._available_cash(account) - self._commission_per_trade
        shares = min(shares, math.floor(available / price) if price > 0 else 0)
        shares = self._cap_by_liquidity(position.symbol, shares)
        if shares <= 0:
            raise InsufficientCashError(f"Can't add to {position.symbol} at ${price:.2f} (available cash: ${available:.2f}).")
        account.current_cash -= shares * price + self._commission_per_trade
        total = position.shares + shares
        position.entry_price = (position.shares * position.entry_price + shares * price) / total
        position.shares = total
        position.fees_paid = (position.fees_paid or 0.0) + self._commission_per_trade
        self._session.add(position)
        self._session.add(account)
        self._session.commit()
        self._session.refresh(position)
        self._record_equity_snapshot()
        self._session.refresh(position)  # _record_equity_snapshot()'s commit expires attributes
        return position

    def _resolve_entry_price(self, symbol: str, planned_entry: float, direction: str) -> float:
        """Fills at the CURRENT market, not at whatever the plan was built on
        however long ago — and refuses outright once the two have diverged
        past MAX_ENTRY_DRIFT_PCT, because at that point the plan's stop
        distance, share count and R:R all describe a trade that is no longer
        on offer. A quote failure falls back to the planned entry rather than
        blocking the open (same graceful-degradation stance as elsewhere)."""
        try:
            # Same reasoning as mark_to_market: filling at a stale quote is a
            # trading decision made on a price that may no longer exist.
            with fresh_data_only():
                market_price = self._data_provider.get_quote(symbol).price
        except AllProvidersFailedError:
            return planned_entry
        if market_price <= 0 or planned_entry <= 0:
            return planned_entry
        drift = abs(market_price - planned_entry) / planned_entry
        if drift > MAX_ENTRY_DRIFT_PCT:
            raise StalePlanError(
                f"{symbol} has moved {drift * 100:.1f}% (plan ${planned_entry:.2f} vs market "
                f"${market_price:.2f}), past the {MAX_ENTRY_DRIFT_PCT * 100:.0f}% limit — "
                "regenerate the trade plan so the stop, size and R:R match the current price."
            )
        return self._slip(market_price, buying=direction == "long")

    def _check_sector_concentration(self, symbol: str, open_positions: list[PaperPosition]) -> None:
        if self._max_positions_per_sector is None:
            return
        sector = get_sector(symbol)
        if sector is None:
            return  # unknown sector (crypto, an off-universe ticker) — nothing to concentrate against
        same_sector = [p for p in open_positions if get_sector(p.symbol) == sector]
        if len(same_sector) >= self._max_positions_per_sector:
            held = ", ".join(p.symbol for p in same_sector)
            raise SectorConcentrationError(
                f"Already holding {len(same_sector)} {sector} position(s) ({held}) — at the "
                f"{self._max_positions_per_sector}-per-sector cap (Settings -> Max Positions Per Sector). "
                "Concentrated positions in one sector are one correlated bet, not independent risk."
            )

    def _cap_by_liquidity(self, symbol: str, shares: int) -> int:
        """Bounds size to a fraction of 20-day average volume. A paper fill is
        infinitely liquid; a real one is not, and a "position" larger than the
        tape could absorb produces returns that could never actually be
        realised. A missing/zero ADV read leaves size untouched rather than
        guessing at a cap."""
        if self._max_position_pct_of_adv is None or shares <= 0:
            return shares
        try:
            adv = self._data_provider.get_quote(symbol).avg_volume_20d
        except AllProvidersFailedError:
            return shares
        if not adv or adv <= 0:
            return shares
        max_shares = math.floor(adv * (self._max_position_pct_of_adv / 100.0))
        return min(shares, max_shares)

    def _available_cash(self, account: AccountState) -> float:
        """current_cash minus collateral reserved for currently open shorts
        — see the comment in open_position() for why raw current_cash isn't
        the right number to size a new position against."""
        open_shorts = self._session.exec(
            select(PaperPosition).where(
                PaperPosition.status == "open",
                PaperPosition.direction == "short",
                self._in_sleeve(PaperPosition.sleeve_id),
            )
        ).all()
        reserved = sum(p.shares * p.entry_price for p in open_shorts)
        return account.current_cash - reserved

    def available_cash(self) -> float:
        """Public read of deployable buying power, for callers that need to
        size a position before opening it (see hold_buy_service)."""
        return self._available_cash(self.get_account_state())

    def close_position(
        self,
        position: PaperPosition,
        close_price: float,
        reason: str,
        *,
        snapshot: bool = True,
        held_bars: pd.DataFrame | None = None,
        last_bar: LastBar = "complete",
        resolution: str | None = None,
        slippage_bps: float | None = None,
    ) -> PaperPosition:
        """`held_bars` / `last_bar` are the bars the position was open for (after the
        entry bar, up to the exit bar) and how the last one relates to the exit, for
        the best/worst-price figures (MFE / MAE) stored on the row. The exit scan
        passes the bars it already walked; without them (a manual close) the bars are
        fetched here, best effort. Recording the excursion can never fail the close.
        `resolution` is how the exit scan placed the exit in time (see
        PaperPosition.exit_resolution); a manual close has none."""
        if not self._owns(position):
            raise ValueError(
                f"Position {position.id} belongs to another sleeve; close it through that sleeve's engine "
                "(closing it here would move the wrong account's cash)."
            )
        self._record_excursion(position, close_price, reason, held_bars, last_bar)
        account = self.get_account_state()
        risk_per_share = abs(position.entry_price - position.stop_loss)
        sign = 1 if position.direction == "long" else -1
        gross_pnl = (close_price - position.entry_price) * position.shares * sign
        # A row that sold part at TP1 before scale-out was retired books that too: one trade, one P&L, one R.
        gross_pnl += position.partial_gross_pnl or 0.0
        # Round-trip cost: whatever was charged at open, plus this exit.
        fees = (position.fees_paid or 0.0) + self._commission_per_trade
        realized_pnl = gross_pnl - fees
        r_shares = position.original_shares or position.shares  # R is per the size at entry, not what is left
        realized_r = realized_pnl / (risk_per_share * r_shares) if risk_per_share > 0 and r_shares > 0 else 0.0

        if position.direction == "long":
            account.current_cash += position.shares * close_price
        else:
            account.current_cash -= position.shares * close_price
        account.current_cash -= self._commission_per_trade

        position.status = "closed"
        position.closed_at = self.now()
        position.close_price = close_price
        position.close_reason = reason
        position.exit_resolution = resolution
        position.exit_slippage_bps = slippage_bps
        position.realized_pnl = realized_pnl
        position.realized_r = realized_r
        position.fees_paid = fees

        self._session.add(position)
        self._session.add(account)
        self._session.commit()
        self._session.refresh(position)
        if snapshot:
            self._record_equity_snapshot()
            self._session.refresh(position)  # _record_equity_snapshot()'s commit expires attributes
        return position

    def close_position_by_hand(self, position: PaperPosition, close_price: float, reason: str) -> PaperPosition:
        """A close the user asks for. Unlike the exit scan (which fills at levels a
        session really touched), a hand close fills at the current quote, so it needs
        the same open-session rule as opening: off-hours the only quote is the last
        session's close, a price nobody could trade at."""
        now = self.now()
        if not is_market_open_for(position.symbol, now):
            raise MarketClosedError(
                f"The US market is closed ({us_closure_reason(now)}), so {position.symbol} can't be closed at a "
                f"real price. It reopens {format_market_time(next_us_open(now))}. A stop or target that "
                f"was touched is still closed by the exit check."
            )
        return self.close_position(position, close_price, reason)

    def _owns(self, position: PaperPosition) -> bool:
        """Whether `position` is in this engine's sleeve (no sleeve_id = core's)."""
        if self._is_core:
            return position.sleeve_id is None or position.sleeve_id == self._scope().sleeve_id
        return position.sleeve_id == self._sleeve_id

    def _record_excursion(
        self,
        position: PaperPosition,
        close_price: float,
        reason: str,
        held_bars: pd.DataFrame | None,
        last_bar: LastBar,
    ) -> None:
        """Sets mfe_pct/mae_pct/mfe_r/mae_r on `position` (not yet committed).
        Any failure leaves them None: the close itself must go through regardless."""
        try:
            if held_bars is None:
                if reason in ("stop_hit", "tp1_hit", "tp2_hit"):
                    return  # which bar the level was hit on is only known to the exit scan
                # A manual (or time) close made right now: the bars to date were all
                # inside the holding period. fresh_data_only: a stale bar set would
                # silently understate the range.
                with fresh_data_only():
                    bars = self._data_provider.get_ohlcv(position.symbol, period=self._exit_scan_period, interval="1d")
                if bars.empty or not self._entry_bar_in_window(bars, position.opened_at):
                    return  # the entry bar isn't in the window: the early bars are unknowable
                held_bars, last_bar = self._bars_after_entry(bars, position.opened_at), "complete"
            result = compute_excursion(
                position.direction,
                position.entry_price,
                position.stop_loss,
                held_bars,
                exit_price=close_price,
                last_bar=last_bar,
            )
            if result is not None:
                position.mfe_pct, position.mae_pct = result.mfe_pct, result.mae_pct
                position.mfe_r, position.mae_r = result.mfe_r, result.mae_r
        except Exception:  # noqa: BLE001 - best effort by design, see docstring
            logger.warning("Could not record MFE/MAE for %s", position.symbol, exc_info=True)

    # -------------------------------------------------------------- marking

    @staticmethod
    def _bars_after_entry(bars: pd.DataFrame, opened_at) -> pd.DataFrame:
        """Bars strictly after the one the position was entered on.

        The entry price is a daily CLOSE, so the entry bar is already spent —
        including it would let that bar's pre-entry low trigger a stop the
        position was never exposed to. Rather than assume the entry bar is
        "today" (wrong whenever a position opens outside US hours, which a
        24/7 crypto pair can do at any hour), it's identified as the last bar
        at or before opened_at, and everything after that is in scope."""
        if bars.empty:
            return bars
        if "date" not in bars.columns:
            # Undated frame: which bars postdate entry is unknowable, so every
            # bar stays in scope. Deliberately the cautious direction — the
            # failure mode this whole method exists to kill is a MISSED stop,
            # which silently deletes losses; an over-eager stop only ever
            # understates results. Both real providers emit `date`.
            return bars
        dates = pd.to_datetime(bars["date"], utc=True, errors="coerce").dt.tz_localize(None)
        entered_on = dates[dates <= pd.Timestamp(opened_at)]
        if entered_on.empty:
            return bars  # every bar postdates the open (fresh position, short window)
        return bars[dates > entered_on.max()]

    @staticmethod
    def _entry_bar_in_window(bars: pd.DataFrame, opened_at) -> bool:
        """Whether the entry bar (the last bar at or before opened_at, see
        _bars_after_entry) is actually in `bars`. Counting trading days since
        entry needs it as day 0: when the window starts AFTER the position
        opened (an undated frame, or a position older than the history the exit
        scan reads) the age is unknowable from these bars, and the time limit
        skips the position rather than guess."""
        if bars.empty or "date" not in bars.columns:
            return False
        dates = pd.to_datetime(bars["date"], utc=True, errors="coerce").dt.tz_localize(None)
        return bool((dates <= pd.Timestamp(opened_at)).any())


    def _scan_hold_exit(self, position: PaperPosition, bars: pd.DataFrame) -> ExitFound | None:
        """Hold mode: the first FINAL daily bar whose CLOSE is beyond the disaster stop closes
        the position at that close (plus slippage). A wick through the stop that closes back
        above it does nothing; a gap-down fills at the close, the honest unfavourable choice.
        Every bar since entry is walked, as in _scan_exit. stop_loss <= 0 = alert-only."""
        if position.stop_loss <= 0:
            return None
        is_long = position.direction == "long"
        raised = position.stop_raised_at.date() if position.stop_raised_at else None  # sell plan raised the stop that day
        for number, (_, bar) in enumerate(bars.iterrows(), start=1):
            if raised is not None and "date" in bar.index and pd.Timestamp(bar["date"]).date() <= raised:
                continue
            close = float(bar["close"])
            beyond = close <= position.stop_loss if is_long else close >= position.stop_loss
            if beyond and self._bar_is_final(position.symbol, bar):
                fill = self._slip(close, buying=not is_long, symbol=position.symbol, shares=position.shares)
                return ExitFound(fill, "disaster_stop", RESOLUTION_DAILY, number, slippage_bps=self._exit_bps(position))
        return None


    def _bar_is_final(self, symbol: str, bar: pd.Series) -> bool:
        """Whether `bar` is finished as of the engine's clock. A bar is read by
        its own calendar date (the date a provider stamps on it, whatever the
        timezone it carries: New York for yfinance equities, UTC for crypto,
        a plain date from Stooq/Nasdaq), and markets.is_daily_bar_final says
        whether that day's session is over. An unreadable date is treated as
        NOT final: the time exit then waits, which can only delay a close."""
        try:
            bar_day = pd.Timestamp(bar["date"]).date()
        except (KeyError, ValueError, TypeError):
            return False
        return is_daily_bar_final(symbol, bar_day, self.now())

    @staticmethod
    def _held_bars(scope: pd.DataFrame, found: ExitFound) -> pd.DataFrame:
        """The bars a position lived through, for its best/worst-price record: the complete daily
        bars before the exit, then the exit bar."""
        return scope.iloc[: found.bars_walked]

    def mark_to_market(self, *, snapshot: bool = True) -> list[PaperPosition]:
        """Closes any open position of this sleeve whose FINAL daily close is beyond its disaster stop
        (see _scan_hold_exit). The take-profit, time-limit and scale-out exits of swing trading are gone: a
        swing row still open in an old database is managed by its stop alone.

        `snapshot=False` lets a read-only caller evaluate exits without
        appending a point to the equity curve - see api/routers/portfolio.py."""
        closed: list[PaperPosition] = []
        open_positions = self._open_positions()  # this sleeve's only: another sleeve's exits are its own engine's job

        for position in open_positions:
            try:
                # fresh_data_only: the provider cache will serve the last known-good response when a
                # fetch fails, which is right for rendering a research page and wrong for deciding an
                # exit. A failed fetch skips the position instead.
                with fresh_data_only():
                    bars = self._data_provider.get_ohlcv(position.symbol, period=self._exit_scan_period, interval="1d")
            except AllProvidersFailedError:
                continue
            if bars.empty:
                continue
            scope = self._bars_after_entry(bars, position.opened_at)
            found = None if scope.empty else self._scan_hold_exit(position, scope)
            if found is not None:
                # snapshot=False: one equity snapshot for the whole sweep below,
                # rather than re-quoting every open position once per close.
                closed.append(
                    self.close_position(
                        position, found.fill_price, found.reason, snapshot=False,
                        held_bars=self._held_bars(scope, found) if self._entry_bar_in_window(bars, position.opened_at) else None,
                        last_bar="complete", resolution=found.resolution, slippage_bps=found.slippage_bps,
                    )
                )

        if snapshot:
            self._record_equity_snapshot()
            for position in closed:
                self._session.refresh(position)
        return closed

    def _record_equity_snapshot(self) -> None:
        account = self.get_account_state()
        open_positions = self._open_positions()

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

        snapshot = EquitySnapshot(
            timestamp=self.now(),
            equity_value=account.current_cash + mark_value,
            cash_balance=account.current_cash,
            sleeve_id=account.sleeve_id,
        )
        self._session.add(snapshot)
        self._session.commit()
