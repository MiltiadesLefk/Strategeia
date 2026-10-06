from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
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
from app.portfolio.intraday import (
    ENTRY_DAY_DAILY_ONLY,
    ENTRY_DAY_DATA_GRACE,
    ENTRY_DAY_HOURLY,
    HOURLY_BAR,
    HOURLY_INTERVAL,
    RESOLUTION_DAILY,
    RESOLUTION_DAILY_AMBIGUOUS,
    RESOLUTION_HOURLY,
    RESOLUTION_HOURLY_AMBIGUOUS,
    HourlyBars,
    bars_after_entry,
    confirms_daily_range,
    day_end,
    entry_hour_row,
    first_touch,
    levels_touched,
    market_day_of,
)
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

# Returns "now" as a naive-UTC datetime, the convention for every stored
# timestamp (timeutil.utcnow_naive).
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class ExitFound:
    """One exit the scan found: the fill, why, how it was placed in time, and
    the bars the position lived through (for the best/worst-price record)."""

    fill_price: float
    reason: str  # "stop_hit" | "tp1_hit" | "time_exit"
    resolution: str  # PaperPosition.exit_resolution
    # Daily bars up to and including the exit bar. For an exit located on an hourly
    # bar the exit day's daily bar is replaced by `hourly` (below), so only the
    # `daily_before` complete daily bars before it count.
    bars_walked: int
    # The hourly bars of the exit day up to and including the exit hour, when the
    # exit was found on one; None for an exit found on a daily bar.
    hourly: pd.DataFrame | None = None
    # Daily bars to count before `hourly` (0 for an entry-day exit).
    daily_before: int = 0
    # Total slippage in bps the market fill paid (flat + liquidity); None when the
    # liquidity model is off or the exit was a limit fill.
    slippage_bps: float | None = None


@dataclass(frozen=True)
class PartialFound:
    """The scan found TP1 on a position that is to scale out: the limit fill of the
    part sold there, how it was placed, and where a later sweep resumes."""

    fill_price: float
    resolution: str
    bar_day: str  # market-time ISO date of the bar (or hour) TP1 was reached on
    at: datetime | None = None  # the hour's start (naive UTC) when found on an hourly bar


@dataclass(frozen=True)
class ScaleOutScan:
    """What one scan of a scale-out position found: the partial sale at TP1 (when it
    happened in this scan) and, independently, a final exit of the remainder."""

    partial: PartialFound | None
    exit: ExitFound | None


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
        max_holding_days: int | None = None,
        intraday_exits: bool = True,
        sleeve: Sleeve | None = None,
        liquidity_slippage_coefficient: float | None = None,
        scale_out_fraction: float | None = None,
        scale_out_stop_mode: str = "breakeven",
        scale_out_trail_r: float = 1.0,
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
        # Partial scale-out at TP1 (None / 0 = off: a position closes fully at TP1, as ever).
        # See _scan_scaled for the rules; the engine is otherwise untouched while this is off.
        self._scale_out_fraction = scale_out_fraction if scale_out_fraction and 0 < scale_out_fraction < 1 else None
        self._scale_out_trail = scale_out_stop_mode == "trail"
        self._scale_out_trail_r = scale_out_trail_r if scale_out_trail_r and scale_out_trail_r > 0 else 1.0
        self._commission_per_trade = commission_per_trade
        self._max_positions_per_sector = max_positions_per_sector
        self._max_position_pct_of_adv = max_position_pct_of_adv
        self._clock = resolve_clock(clock)
        # Trading bars a position may stay open before it is closed at that
        # bar's close; None or 0 = no limit. See _first_exit.
        self._max_holding_days = max_holding_days if max_holding_days and max_holding_days > 0 else None
        # Whether the exit scan may fetch hourly bars to order a stop and a target
        # inside one daily bar and to check the rest of the entry day. On for the
        # live app. A caller that replays history with a provider that has no hourly
        # bars for the simulated dates (a backtest) turns it off and keeps the plain
        # daily rules, rather than asking for bars it cannot get on every step.
        self._intraday_exits = intraday_exits

    def now(self) -> datetime:
        """The engine's current moment (naive UTC): real time, or a simulated
        one when a clock was passed in."""
        return self._clock()

    def is_market_open_for(self, symbol: str) -> bool:
        """Whether open_position would accept `symbol` right now, by the
        engine's own clock — lets a caller decide what to do with a plan
        (trade_plan_service defers it) before attempting the fill."""
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

    def open_position(self, trade_plan: TradePlanRecord) -> PaperPosition:
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
        size a position before opening it (see trade_plan_service)."""
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
        # A scaled-out position books what it sold at TP1 too: one trade, one P&L, one R.
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
                    bars = self._data_provider.get_ohlcv(position.symbol, period=EXIT_SCAN_PERIOD, interval="1d")
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

    def _first_exit(
        self, position: PaperPosition, bars: pd.DataFrame, *, holding_limit: int | None = None
    ) -> tuple[float, str] | None:
        """(fill price, reason) of the first exit on the daily `bars`, or None.
        See _scan_exit; this never looks at hourly bars."""
        found = self._scan_exit(position, bars, holding_limit=holding_limit)
        return None if found is None else (found.fill_price, found.reason)

    def _scan_exit(
        self,
        position: PaperPosition,
        bars: pd.DataFrame,
        *,
        holding_limit: int | None = None,
        hourly: HourlyBars | None = None,
    ) -> ExitFound | None:
        """The first exit on the daily `bars` (chronological), or None.

        Walks bars in chronological order and returns the FIRST stop/TP1
        touch, not merely whatever the latest bar happens to show.

        Checking only the most recent bar (the previous behaviour) silently
        dropped every exit that happened and then reversed: a position could
        trade clean through its stop, recover, and still sit open at a paper
        profit. That error is one-directional — it only ever deletes losses —
        so it inflated win rate and total return together.

        Within a single bar, stop is checked before TP1: when both levels sit
        inside one bar's range, daily OHLC cannot say which came first, so the
        engine takes the unfavourable branch rather than the flattering one.
        With `hourly` (that symbol's hourly bars) such a day is looked at hour
        by hour first, and the first level touched wins; the same unfavourable
        rule applies one level down (both in one hour: the stop), and without
        usable hourly bars for that day the daily rule stands and the exit is
        marked as having been decided that way. A bar that OPENS beyond the stop
        needs no hourly look: the open itself triggered the stop.

        `holding_limit` (trading bars since the entry bar, which is bar 0 and
        is not in `bars`) adds the third exit, a time limit: on the first bar
        numbered at or past it, a position that neither the stop nor TP1
        touched is closed at that bar's CLOSE. It comes last on purpose: on the
        limit bar the stop and TP1 are still checked first, because a level
        touched during the day happened before the close the time exit acts on.
        It fills at the close like the entry does (the decision is made on a
        finished bar), as a market order, so it pays slippage like a stop. A bar
        that has not finished yet (today's, while the session runs) can never
        trigger it: acting on its "close" would be acting on a price the day
        then moves away from. That bar is also the last one, so the position
        simply waits for a later run."""
        is_long = position.direction == "long"
        for number, (_, bar) in enumerate(bars.iterrows(), start=1):
            high, low, bar_open = float(bar["high"]), float(bar["low"]), float(bar["open"])
            stop_touched, tp_touched = levels_touched(position.direction, position.stop_loss, position.tp1, high, low)
            if stop_touched or tp_touched:
                resolution = RESOLUTION_DAILY
                if stop_touched and tp_touched:
                    opens_beyond_stop = bar_open <= position.stop_loss if is_long else bar_open >= position.stop_loss
                    if not opens_beyond_stop:
                        located = self._locate_in_hours(position, bar, hourly) if hourly is not None else None
                        if located is not None:
                            return ExitFound(
                                located.fill_price,
                                located.reason,
                                located.resolution,
                                number,
                                hourly=located.hourly,
                                daily_before=number - 1,
                                slippage_bps=self._exit_bps(position, market=located.reason == "stop_hit"),
                            )
                        resolution = RESOLUTION_DAILY_AMBIGUOUS
                if stop_touched:
                    fill = self._exit_fill_price(position.direction, bar_open, position.stop_loss, is_stop=True)
                    fill = self._slip(fill, buying=not is_long, symbol=position.symbol, shares=position.shares)
                    return ExitFound(fill, "stop_hit", resolution, number, slippage_bps=self._exit_bps(position))
                fill = self._exit_fill_price(position.direction, bar_open, position.tp1, is_stop=False)
                return ExitFound(fill, "tp1_hit", resolution, number)
            if holding_limit is not None and number >= holding_limit and self._bar_is_final(position.symbol, bar):
                fill = self._slip(
                    float(bar["close"]), buying=position.direction == "short", symbol=position.symbol, shares=position.shares
                )
                return ExitFound(fill, "time_exit", RESOLUTION_DAILY, number, slippage_bps=self._exit_bps(position))
        return None

    def _hourly_fill(
        self,
        position: PaperPosition,
        hour_open: float,
        reason: str,
        *,
        stop: float | None = None,
        target: float | None = None,
        shares: float | None = None,
    ) -> float:
        """Fill for a level touched on an hourly bar: the same gap rules as a daily
        bar, with the hour's own open (a stop pays slippage, a target is a limit).
        `stop` / `target` / `shares` override the position's own for a scaled-out runner."""
        is_stop = reason == "stop_hit"
        level = (position.stop_loss if stop is None else stop) if is_stop else (position.tp1 if target is None else target)
        fill = self._exit_fill_price(position.direction, hour_open, level, is_stop=is_stop)
        if not is_stop:
            return fill
        return self._slip(
            fill, buying=position.direction == "short", symbol=position.symbol,
            shares=position.shares if shares is None else shares,
        )

    def _locate_in_hours(
        self,
        position: PaperPosition,
        bar: pd.Series,
        hourly: HourlyBars,
        *,
        stop: float | None = None,
        target: float | None = None,
        shares: float | None = None,
    ) -> ExitFound | None:
        """Which level a daily bar that held BOTH of them reached first, from that
        day's hourly bars; None when that cannot be said honestly (no hourly data
        for the day, or the hours do not themselves reach both levels, so one is
        missing and the order they show may be wrong)."""
        try:
            bar_day = pd.Timestamp(bar["date"]).date()
        except (KeyError, ValueError, TypeError):
            return None
        stop_level = position.stop_loss if stop is None else stop
        target_level = position.tp1 if target is None else target
        hours = hourly.day_rows(bar_day)
        if hours is None or not confirms_daily_range(position.direction, stop_level, target_level, hours):
            return None
        touch = first_touch(position.direction, stop_level, target_level, hours)
        if touch is None:
            return None
        fill = self._hourly_fill(
            position, float(hours["open"].iloc[touch.row]), touch.reason, stop=stop, target=target, shares=shares
        )
        resolution = RESOLUTION_HOURLY_AMBIGUOUS if touch.both else RESOLUTION_HOURLY
        return ExitFound(
            fill, touch.reason, resolution, 0, hourly=hours.iloc[: touch.row + 1],
            slippage_bps=self._exit_bps(position, market=touch.reason == "stop_hit", shares=shares),
        )

    # ------------------------------------------------------------- scale-out

    def _split_shares(self, shares: int) -> tuple[int, int] | None:
        """(sold at TP1, kept) for a position of `shares`, or None when scale-out is
        off or the position is too small to split into two whole, non-empty parts
        (it then exits fully at TP1 like any other)."""
        if self._scale_out_fraction is None:
            return None
        sold = math.floor(shares * self._scale_out_fraction + 1e-9)
        if sold < 1 or shares - sold < 1:
            return None
        return sold, shares - sold

    def _is_scaled(self, position: PaperPosition) -> bool:
        """Whether the scale-out scan rules this position: it already sold part at TP1
        (whatever the setting says now: a runner is managed to its end), or scale-out
        is on and it can be split. Everything else takes the unchanged full-exit path."""
        return position.partial_fill_price is not None or self._split_shares(position.shares) is not None

    def _runner_params(self, position: PaperPosition) -> tuple[float, float | None]:
        """(the remainder's starting stop, the trail distance or None) once TP1 sells
        part: breakeven is the entry fill. A recorded partial keeps what it was given
        at the time; a first one takes the engine's current mode."""
        if position.partial_fill_price is not None:
            stop0 = position.runner_stop if position.runner_stop is not None else position.entry_price
            return stop0, position.runner_trail_distance
        risk = abs(position.entry_price - position.stop_loss)
        distance = risk * self._scale_out_trail_r if self._scale_out_trail and risk > 0 else None
        return position.entry_price, distance

    @staticmethod
    def _runner_stop_level(direction: str, stop0: float, distance: float | None, best: float | None) -> float:
        """The remainder's stop going into a bar: breakeven, or in trail mode the best
        price of the EARLIER bars minus the trail distance (never worse than breakeven).
        The bar's own extreme is not used for its own stop: the daily bar cannot say
        whether the high came before the low."""
        if distance is None or best is None:
            return stop0
        return max(stop0, best - distance) if direction == "long" else min(stop0, best + distance)

    @staticmethod
    def _bar_day(bar: pd.Series):
        try:
            return pd.Timestamp(bar["date"]).date()
        except (KeyError, ValueError, TypeError):
            return None

    def _apply_partial(self, position: PaperPosition, found: PartialFound) -> None:
        """Books the part sold at TP1: its cash (a limit fill, so no slippage), its
        profit, and the remainder's new stop, all on the SAME position row. After this
        `shares` is what is still held. Keeps the attribute-expiry refresh pattern."""
        split = self._split_shares(position.shares)
        if split is None:
            return
        sold, kept = split
        sign = 1 if position.direction == "long" else -1
        stop0, distance = self._runner_params(position)
        account = self.get_account_state()
        if position.direction == "long":
            account.current_cash += sold * found.fill_price
        else:
            account.current_cash -= sold * found.fill_price
        account.current_cash -= self._commission_per_trade
        position.original_shares = position.shares
        position.partial_shares = sold
        position.shares = kept
        position.partial_fill_price = found.fill_price
        position.partial_gross_pnl = (found.fill_price - position.entry_price) * sold * sign
        position.partial_resolution = found.resolution
        position.partial_bar_day = found.bar_day
        position.partial_at = found.at
        position.runner_stop = stop0
        position.runner_trail_distance = distance
        position.fees_paid = (position.fees_paid or 0.0) + self._commission_per_trade
        self._session.add(position)
        self._session.add(account)
        self._session.commit()
        self._session.refresh(position)  # commit() expires attributes

    def _kept_shares(self, position: PaperPosition) -> int:
        """What the runner holds: the shares left after a recorded partial, or what the
        split of a not-yet-sold position would leave."""
        if position.partial_fill_price is not None:
            return position.shares
        return (self._split_shares(position.shares) or (0, position.shares))[1]

    def _runner_hour_exit(
        self, position: PaperPosition, hours: pd.DataFrame, touch, base_row: int, stop0: float, number: int
    ) -> ExitFound:
        """The remainder's exit located on an hourly bar of the partial day (`touch` is a
        first_touch over hours[base_row:], whose first row is the TP1 hour)."""
        abs_row = base_row + touch.row
        reason = "stop_hit" if touch.reason == "stop_hit" else "tp2_hit"
        kept = self._kept_shares(position)
        # Row 0 is the hour TP1 was reached in: a stop there is placed after the partial, so it
        # fills at its own level, not the hour's open (which came before the partial).
        hour_open = stop0 if touch.in_entry_hour else float(hours["open"].iloc[abs_row])
        fill = self._hourly_fill(position, hour_open, touch.reason, stop=stop0, target=position.tp2, shares=kept)
        return ExitFound(
            fill, reason, RESOLUTION_HOURLY_AMBIGUOUS if touch.both else RESOLUTION_HOURLY, number,
            hourly=hours.iloc[: abs_row + 1], daily_before=number - 1,
            slippage_bps=self._exit_bps(position, market=reason == "stop_hit", shares=kept),
        )

    def _scan_scaled(
        self,
        position: PaperPosition,
        bars: pd.DataFrame,
        *,
        holding_limit: int | None = None,
        hourly: HourlyBars | None = None,
    ) -> ScaleOutScan:
        """_scan_exit for a position that scales out at TP1 (see _is_scaled).

        Stage 1 (nothing sold yet) is _scan_exit's walk with one change: TP1 sells
        part instead of closing, then the SAME bar is read as the first bar of stage 2.
        Stage 2 (the remainder, "runner") has its own stop (breakeven, or trailing) and
        its target is TP2. Every invariant of the full-exit scan carries over: every bar
        since entry is walked (a resumed runner restarts at its partial's day, never
        later), a stop is a market order that fills at a gap-open, a target is a limit
        that fills at a gap-open, the stop is taken before the target within a bar (hourly
        bars settle a day holding both), and the time limit closes what neither touched
        at a finished bar's close.

        One honest assumption: on the bar TP1 was reached the daily range cannot say if
        its low came before or after TP1, so a low through the runner's stop counts as
        coming after (the runner is stopped at its level, no gap fill). That is the
        unfavourable branch, as everywhere else in the engine."""
        direction = position.direction
        is_long = direction == "long"
        stage2 = position.partial_fill_price is not None
        stop0, distance = self._runner_params(position)
        partial_day = position.partial_bar_day
        kept = self._kept_shares(position)
        partial: PartialFound | None = None
        best: float | None = None  # best price of the runner's earlier bars, for the trail
        for number, (_, bar) in enumerate(bars.iterrows(), start=1):
            high, low, bar_open = float(bar["high"]), float(bar["low"]), float(bar["open"])
            bar_day = self._bar_day(bar)
            if stage2 and partial_day is not None and bar_day is not None and bar_day.isoformat() < partial_day:
                continue  # before the partial: stage 1 already walked these
            first_runner_bar = False
            if not stage2:
                stop_touched, tp_touched = levels_touched(direction, position.stop_loss, position.tp1, high, low)
                if not (stop_touched or tp_touched):
                    if holding_limit is not None and number >= holding_limit and self._bar_is_final(position.symbol, bar):
                        fill = self._slip(
                            float(bar["close"]), buying=not is_long, symbol=position.symbol, shares=position.shares
                        )
                        return ScaleOutScan(
                            None, ExitFound(fill, "time_exit", RESOLUTION_DAILY, number, slippage_bps=self._exit_bps(position))
                        )
                    continue
                resolution = RESOLUTION_DAILY
                if stop_touched and tp_touched:
                    opens_beyond_stop = bar_open <= position.stop_loss if is_long else bar_open >= position.stop_loss
                    if not opens_beyond_stop:
                        located = self._locate_in_hours(position, bar, hourly) if hourly is not None else None
                        if located is not None:
                            if located.reason == "stop_hit":
                                return ScaleOutScan(None, replace(located, bars_walked=number, daily_before=number - 1))
                            # TP1 first, found on the hours: sell there, read the rest of the day for the runner.
                            hours = located.hourly
                            row = len(hours) - 1
                            at = pd.Timestamp(hours["start"].iloc[-1]).to_pydatetime()
                            partial = PartialFound(located.fill_price, located.resolution, bar_day.isoformat(), at)
                            day_hours = hourly.day_rows(bar_day)
                            if day_hours is not None:
                                tail = day_hours.iloc[row:].reset_index(drop=True)
                                touch = first_touch(direction, stop0, position.tp2, tail, entry_row=0)
                                if touch is not None:
                                    return ScaleOutScan(
                                        partial, self._runner_hour_exit(position, day_hours, touch, row, stop0, number)
                                    )
                            stage2, partial_day, first_runner_bar = True, bar_day.isoformat(), True
                        else:
                            resolution = RESOLUTION_DAILY_AMBIGUOUS
                    # (opens beyond the stop: the open itself triggered it, no hourly look)
                if not stage2:
                    if stop_touched:
                        fill = self._exit_fill_price(direction, bar_open, position.stop_loss, is_stop=True)
                        fill = self._slip(fill, buying=not is_long, symbol=position.symbol, shares=position.shares)
                        return ScaleOutScan(
                            None, ExitFound(fill, "stop_hit", resolution, number, slippage_bps=self._exit_bps(position))
                        )
                    fill = self._exit_fill_price(direction, bar_open, position.tp1, is_stop=False)
                    if bar_day is None:
                        # Without the bar's date a resumed runner could not be placed in time: exit fully.
                        return ScaleOutScan(None, ExitFound(fill, "tp1_hit", resolution, number))
                    partial = PartialFound(fill, resolution, bar_day.isoformat())
                    stage2, partial_day, first_runner_bar = True, bar_day.isoformat(), True
            else:
                first_runner_bar = bar_day is not None and partial_day is not None and bar_day.isoformat() == partial_day

            # ---- stage 2: the remainder, on this bar
            stop_level = self._runner_stop_level(direction, stop0, distance, best)
            stop_touched, tp_touched = levels_touched(direction, stop_level, position.tp2, high, low)
            if stop_touched:
                resolution = RESOLUTION_DAILY
                if tp_touched:
                    opens_beyond_stop = bar_open <= stop_level if is_long else bar_open >= stop_level
                    if not first_runner_bar and not opens_beyond_stop:
                        located = (
                            self._locate_in_hours(position, bar, hourly, stop=stop_level, target=position.tp2, shares=kept)
                            if hourly is not None
                            else None
                        )
                        if located is not None:
                            reason = "stop_hit" if located.reason == "stop_hit" else "tp2_hit"
                            return ScaleOutScan(
                                partial, replace(located, reason=reason, bars_walked=number, daily_before=number - 1)
                            )
                    resolution = RESOLUTION_DAILY_AMBIGUOUS
                # On the partial bar the stop did not exist before TP1: no gap fill, its own level.
                fill = stop_level if first_runner_bar else self._exit_fill_price(direction, bar_open, stop_level, is_stop=True)
                fill = self._slip(fill, buying=not is_long, symbol=position.symbol, shares=kept)
                return ScaleOutScan(
                    partial, ExitFound(fill, "stop_hit", resolution, number, slippage_bps=self._exit_bps(position, shares=kept))
                )
            if tp_touched:
                fill = self._exit_fill_price(direction, bar_open, position.tp2, is_stop=False)
                return ScaleOutScan(partial, ExitFound(fill, "tp2_hit", RESOLUTION_DAILY, number))
            if holding_limit is not None and number >= holding_limit and self._bar_is_final(position.symbol, bar):
                fill = self._slip(float(bar["close"]), buying=not is_long, symbol=position.symbol, shares=kept)
                return ScaleOutScan(
                    partial, ExitFound(fill, "time_exit", RESOLUTION_DAILY, number, slippage_bps=self._exit_bps(position, shares=kept))
                )
            extreme = high if is_long else low
            best = extreme if best is None else (max(best, extreme) if is_long else min(best, extreme))
        return ScaleOutScan(partial, None)

    def _scan_entry_day(self, position: PaperPosition, hourly: HourlyBars) -> ExitFound | None:
        """Checks the part of the ENTRY day after the position existed, hour by hour.

        The daily walk skips the entry bar on purpose (entry is priced at that
        day's close or quote, so the bar's earlier low must not stop out a position
        that did not exist yet). Positions now open mid-session, so a stop or target
        touched LATER the same day would never be seen, and a missed stop only ever
        deletes losses. The hourly bars cover that remainder.

        The hour the position was opened in is partly before the entry, and an
        hourly bar cannot say which part its extremes came from. Only the STOP is
        checked in it (a stop touched before the entry costs a trade that was never
        exposed, which can only understate results); a target there is ignored (it
        may have been reached before the entry, and crediting that is the flattering
        direction). A stop in that hour fills at the stop price, not the bar's open,
        which is before the position existed. Every later hour is checked in full.

        Records on the position how far the check got (entry_day_check): "hourly"
        once the whole day is covered, "daily_only" when the hourly bars never
        arrived or stayed incomplete past a day's grace (the entry day then stays
        unchecked, as before, and the row says so). Until one of those, nothing is
        recorded and the next sweep looks again. Returns the exit found, if any."""
        symbol, opened_at, now = position.symbol, pd.Timestamp(position.opened_at), self.now()
        day = market_day_of(symbol, position.opened_at)
        end = day_end(symbol, day)
        if end is None:
            self._record_entry_day_check(position, ENTRY_DAY_DAILY_ONLY)  # no session that day: no hours exist
            return None
        rows = hourly.day_rows(day)
        if rows is None:
            if now >= end + ENTRY_DAY_DATA_GRACE:
                self._record_entry_day_check(position, ENTRY_DAY_DAILY_ONLY)
            return None
        after = bars_after_entry(rows, opened_at)
        entry_row = entry_hour_row(after, opened_at)
        if position.partial_fill_price is not None:
            # A runner already sold part at TP1: resume from that hour with the runner's rules.
            if position.partial_at is None:
                # The partial came on a later daily bar, after this day: nothing of it is left to check.
                self._record_entry_day_check(position, ENTRY_DAY_DAILY_ONLY)
                return None
            at = pd.Timestamp(position.partial_at)
            starts = [row for row, start in enumerate(after["start"]) if start >= at]
            if not starts:
                return self._entry_day_tail(position, rows, end, now)
            return self._entry_day_runner(position, rows, after, entry_row, starts[0], end, now)
        touch = first_touch(position.direction, position.stop_loss, position.tp1, after, entry_row=entry_row)
        if touch is not None and touch.reason == "tp1_hit" and self._split_shares(position.shares) is not None:
            # Scale-out: TP1 sells part (a limit, filled at the hour's open or better), the rest runs on.
            fill = self._hourly_fill(position, float(after["open"].iloc[touch.row]), "tp1_hit")
            at = pd.Timestamp(after["start"].iloc[touch.row]).to_pydatetime()
            self._apply_partial(position, PartialFound(fill, RESOLUTION_HOURLY, day.isoformat(), at))
            return self._entry_day_runner(position, rows, after, entry_row, touch.row, end, now)
        if touch is not None:
            position.entry_day_check = ENTRY_DAY_HOURLY  # saved with the close
            if touch.in_entry_hour:
                fill = self._hourly_fill(position, position.stop_loss, "stop_hit")  # at the level: see docstring
                held = after.iloc[0:0]
            else:
                fill = self._hourly_fill(position, float(after["open"].iloc[touch.row]), touch.reason)
                held = after.iloc[(0 if entry_row is None else entry_row + 1) : touch.row + 1]
            resolution = RESOLUTION_HOURLY_AMBIGUOUS if touch.both else RESOLUTION_HOURLY
            return ExitFound(
                fill, touch.reason, resolution, 0, hourly=held,
                slippage_bps=self._exit_bps(position, market=touch.reason == "stop_hit"),
            )
        return self._entry_day_tail(position, rows, end, now)

    def _entry_day_tail(self, position: PaperPosition, rows: pd.DataFrame, end: datetime, now: datetime) -> None:
        """Bookkeeping when the entry day's hours showed no exit: covered, or still waiting."""
        if now >= end and rows["start"].iloc[-1] + HOURLY_BAR >= end:
            self._record_entry_day_check(position, ENTRY_DAY_HOURLY)
        elif now >= end + ENTRY_DAY_DATA_GRACE:
            self._record_entry_day_check(position, ENTRY_DAY_DAILY_ONLY)
        return None

    def _entry_day_runner(
        self,
        position: PaperPosition,
        rows: pd.DataFrame,
        after: pd.DataFrame,
        entry_row: int | None,
        start_row: int,
        end: datetime,
        now: datetime,
    ) -> ExitFound | None:
        """The rest of the entry day for a runner (the part left after TP1), hour by
        hour from `start_row`, the hour TP1 was reached in. In that hour only the
        runner's stop can fire (it is placed after TP1, so it fills at its own level);
        TP2 and the stop count in full from the next hour on."""
        stop0, _ = self._runner_params(position)
        tail = after.iloc[start_row:].reset_index(drop=True)
        touch = first_touch(position.direction, stop0, position.tp2, tail, entry_row=0)
        if touch is None:
            return self._entry_day_tail(position, rows, end, now)
        position.entry_day_check = ENTRY_DAY_HOURLY  # saved with the close
        abs_row = start_row + touch.row
        reason = "stop_hit" if touch.reason == "stop_hit" else "tp2_hit"
        hour_open = stop0 if touch.in_entry_hour else float(after["open"].iloc[abs_row])
        fill = self._hourly_fill(position, hour_open, touch.reason, stop=stop0, target=position.tp2)
        held = after.iloc[(0 if entry_row is None else entry_row + 1) : abs_row + 1]
        return ExitFound(
            fill, reason, RESOLUTION_HOURLY_AMBIGUOUS if touch.both else RESOLUTION_HOURLY, 0, hourly=held,
            slippage_bps=self._exit_bps(position, market=reason == "stop_hit"),
        )

    def _record_entry_day_check(self, position: PaperPosition, status: str) -> None:
        position.entry_day_check = status
        self._session.add(position)
        self._session.commit()

    def _fetch_hourly(self, symbol: str, period: str) -> pd.DataFrame:
        # fresh_data_only: an exit decision made on a cached hourly frame is the same
        # stale-data failure the daily fetch guards against.
        with fresh_data_only():
            return self._data_provider.get_ohlcv(symbol, period=period, interval=HOURLY_INTERVAL)

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
    def _held_bars(scope: pd.DataFrame | None, found: ExitFound) -> pd.DataFrame:
        """The bars a position lived through, for its best/worst-price record: the
        complete daily bars before the exit, then either the exit day's daily bar
        or, for an exit found on an hourly bar, that day's hours up to the exit hour
        (the last of which the record treats as the exit bar)."""
        if found.hourly is None:
            return scope.iloc[: found.bars_walked]
        columns = ["open", "high", "low"]
        before = scope.iloc[: found.daily_before][columns] if scope is not None else found.hourly.iloc[0:0][columns]
        return pd.concat([before, found.hourly[columns]], ignore_index=True)

    def mark_to_market(self, *, snapshot: bool = True) -> list[PaperPosition]:
        """Closes any open position whose stop or TP1 was touched on any bar
        since entry, or that has run out its holding limit (max_holding_days).
        v1 exit rule: whichever of stop or TP1 hits first closes the full
        position; TP2 is informational only. With scale-out ON (opt-in, see
        _scan_scaled) TP1 instead sells part and the rest runs to TP2, its own
        stop or the time limit. The time
        limit is the third way out and only ever applies when neither level was
        touched (see _first_exit).

        Hourly bars sharpen two daily-bar blind spots, fetched only when needed
        (see _scan_exit and _scan_entry_day): which level a day holding both
        reached first, and what happened later on the entry day itself.

        `snapshot=False` lets a read-only caller evaluate exits without
        appending a point to the equity curve — see api/routers/portfolio.py."""
        closed: list[PaperPosition] = []
        open_positions = self._open_positions()  # this sleeve's only: another sleeve's exits are its own engine's job
        # One hourly history per symbol per sweep, fetched only when a position needs
        # it: the entry day not yet checked, or a daily bar holding both levels.
        hourly_books: dict[str, HourlyBars] = {}

        for position in open_positions:
            try:
                # fresh_data_only: the provider cache will serve the last
                # known-good response when a fetch fails, which is right for
                # rendering a research page and wrong for deciding an exit —
                # closing a position against a bar from days ago is the same
                # stale-data failure the multi-bar rewrite above exists to
                # prevent. A failed fetch skips the position instead.
                with fresh_data_only():
                    bars = self._data_provider.get_ohlcv(position.symbol, period=EXIT_SCAN_PERIOD, interval="1d")
            except AllProvidersFailedError:
                continue
            if bars.empty:
                continue
            hourly = None
            if self._intraday_exits:
                symbol = position.symbol
                hourly = hourly_books.get(symbol)
                if hourly is None:
                    hourly = hourly_books[symbol] = HourlyBars(
                        symbol, self.now(), lambda period, symbol=symbol: self._fetch_hourly(symbol, period)
                    )
            # The entry day comes before every daily bar in time, so it is checked first.
            found = None
            if hourly is not None and position.entry_day_check is None:
                found = self._scan_entry_day(position, hourly)
            if found is not None:
                held = self._held_bars(None, found)  # an entry-day exit: the hours are the whole record
            else:
                scope = self._bars_after_entry(bars, position.opened_at)
                if scope.empty:
                    continue
                # The time limit counts bars from the entry bar, so it only applies
                # when that bar is in the window (see _entry_bar_in_window).
                entry_in_window = self._entry_bar_in_window(bars, position.opened_at)
                holding_limit = self._max_holding_days if entry_in_window else None
                if self._is_scaled(position):
                    scan = self._scan_scaled(position, scope, holding_limit=holding_limit, hourly=hourly)
                    if scan.partial is not None and position.partial_fill_price is None:
                        self._apply_partial(position, scan.partial)
                    found = scan.exit
                else:
                    found = self._scan_exit(position, scope, holding_limit=holding_limit, hourly=hourly)
                if found is None:
                    continue
                # The bars up to the exit bar are what the position lived through (for the
                # best/worst-price record); a stop or TP1 hit happened part-way through its
                # bar, a time exit is the finished bar's close. Only when the entry bar is in
                # the window: otherwise the early bars of the trade are missing and the
                # range would be understated.
                held = self._held_bars(scope, found) if entry_in_window else None
            # snapshot=False: one equity snapshot for the whole sweep below,
            # rather than re-quoting every open position once per close.
            closed.append(
                self.close_position(
                    position,
                    found.fill_price,
                    found.reason,
                    snapshot=False,
                    held_bars=held,
                    last_bar="complete" if found.reason == "time_exit" else "exit",
                    resolution=found.resolution,
                    slippage_bps=found.slippage_bps,
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
