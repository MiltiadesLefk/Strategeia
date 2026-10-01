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
from app.portfolio.models import AccountState, EquitySnapshot, PaperPosition, TradePlanRecord
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


class PaperTradingEngine:
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
    ):
        self._session = session
        self._data_provider = data_provider
        self._starting_cash = starting_cash
        self._max_concurrent_positions = max_concurrent_positions
        self._slippage_bps = slippage_bps
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

    def get_account_state(self) -> AccountState:
        account = self._session.exec(select(AccountState)).first()
        if account is None:
            account = AccountState(starting_cash=self._starting_cash, current_cash=self._starting_cash)
            self._session.add(account)
            self._session.commit()
            self._session.refresh(account)
        return account

    # ----------------------------------------------------------------- fills

    def _slip(self, price: float, *, buying: bool) -> float:
        """Market-order fills cross the spread and move with the book; this
        nudges the fill against the account by slippage_bps. Applied to
        entries and to stop exits (both market orders) — never to a
        take-profit, which is a resting limit order that fills at its price
        or better by construction."""
        if self._slippage_bps <= 0:
            return price
        factor = self._slippage_bps / 10_000.0
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

        account = self.get_account_state()

        existing = self._session.exec(
            select(PaperPosition).where(PaperPosition.symbol == trade_plan.symbol, PaperPosition.status == "open")
        ).first()
        if existing is not None:
            raise DuplicatePositionError(f"{trade_plan.symbol} already has an open position (id={existing.id})")

        open_positions = self._session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all()

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
            select(PaperPosition).where(PaperPosition.status == "open", PaperPosition.direction == "short")
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
    ) -> PaperPosition:
        """`held_bars` / `last_bar` are the bars the position was open for (after the
        entry bar, up to the exit bar) and how the last one relates to the exit, for
        the best/worst-price figures (MFE / MAE) stored on the row. The exit scan
        passes the bars it already walked; without them (a manual close) the bars are
        fetched here, best effort. Recording the excursion can never fail the close.
        `resolution` is how the exit scan placed the exit in time (see
        PaperPosition.exit_resolution); a manual close has none."""
        self._record_excursion(position, close_price, reason, held_bars, last_bar)
        account = self.get_account_state()
        risk_per_share = abs(position.entry_price - position.stop_loss)
        sign = 1 if position.direction == "long" else -1
        gross_pnl = (close_price - position.entry_price) * position.shares * sign
        # Round-trip cost: whatever was charged at open, plus this exit.
        fees = (position.fees_paid or 0.0) + self._commission_per_trade
        realized_pnl = gross_pnl - fees
        realized_r = realized_pnl / (risk_per_share * position.shares) if risk_per_share > 0 and position.shares > 0 else 0.0

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
                if reason in ("stop_hit", "tp1_hit"):
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
                            )
                        resolution = RESOLUTION_DAILY_AMBIGUOUS
                if stop_touched:
                    fill = self._exit_fill_price(position.direction, bar_open, position.stop_loss, is_stop=True)
                    return ExitFound(self._slip(fill, buying=not is_long), "stop_hit", resolution, number)
                fill = self._exit_fill_price(position.direction, bar_open, position.tp1, is_stop=False)
                return ExitFound(fill, "tp1_hit", resolution, number)
            if holding_limit is not None and number >= holding_limit and self._bar_is_final(position.symbol, bar):
                fill = self._slip(float(bar["close"]), buying=position.direction == "short")
                return ExitFound(fill, "time_exit", RESOLUTION_DAILY, number)
        return None

    def _hourly_fill(self, position: PaperPosition, hour_open: float, reason: str) -> float:
        """Fill for a level touched on an hourly bar: the same gap rules as a daily
        bar, with the hour's own open (a stop pays slippage, a target is a limit)."""
        is_stop = reason == "stop_hit"
        level = position.stop_loss if is_stop else position.tp1
        fill = self._exit_fill_price(position.direction, hour_open, level, is_stop=is_stop)
        return self._slip(fill, buying=position.direction == "short") if is_stop else fill

    def _locate_in_hours(self, position: PaperPosition, bar: pd.Series, hourly: HourlyBars) -> ExitFound | None:
        """Which level a daily bar that held BOTH of them reached first, from that
        day's hourly bars; None when that cannot be said honestly (no hourly data
        for the day, or the hours do not themselves reach both levels, so one is
        missing and the order they show may be wrong)."""
        try:
            bar_day = pd.Timestamp(bar["date"]).date()
        except (KeyError, ValueError, TypeError):
            return None
        hours = hourly.day_rows(bar_day)
        if hours is None or not confirms_daily_range(position.direction, position.stop_loss, position.tp1, hours):
            return None
        touch = first_touch(position.direction, position.stop_loss, position.tp1, hours)
        if touch is None:
            return None
        fill = self._hourly_fill(position, float(hours["open"].iloc[touch.row]), touch.reason)
        resolution = RESOLUTION_HOURLY_AMBIGUOUS if touch.both else RESOLUTION_HOURLY
        return ExitFound(fill, touch.reason, resolution, 0, hourly=hours.iloc[: touch.row + 1])

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
        touch = first_touch(position.direction, position.stop_loss, position.tp1, after, entry_row=entry_row)
        if touch is not None:
            position.entry_day_check = ENTRY_DAY_HOURLY  # saved with the close
            if touch.in_entry_hour:
                fill = self._hourly_fill(position, position.stop_loss, "stop_hit")  # at the level: see docstring
                held = after.iloc[0:0]
            else:
                fill = self._hourly_fill(position, float(after["open"].iloc[touch.row]), touch.reason)
                held = after.iloc[(0 if entry_row is None else entry_row + 1) : touch.row + 1]
            resolution = RESOLUTION_HOURLY_AMBIGUOUS if touch.both else RESOLUTION_HOURLY
            return ExitFound(fill, touch.reason, resolution, 0, hourly=held)
        if now >= end and rows["start"].iloc[-1] + HOURLY_BAR >= end:
            self._record_entry_day_check(position, ENTRY_DAY_HOURLY)
        elif now >= end + ENTRY_DAY_DATA_GRACE:
            self._record_entry_day_check(position, ENTRY_DAY_DAILY_ONLY)
        return None

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
        position; TP2 is informational only (no partial scale-out). The time
        limit is the third way out and only ever applies when neither level was
        touched (see _first_exit).

        Hourly bars sharpen two daily-bar blind spots, fetched only when needed
        (see _scan_exit and _scan_entry_day): which level a day holding both
        reached first, and what happened later on the entry day itself.

        `snapshot=False` lets a read-only caller evaluate exits without
        appending a point to the equity curve — see api/routers/portfolio.py."""
        closed: list[PaperPosition] = []
        open_positions = self._session.exec(select(PaperPosition).where(PaperPosition.status == "open")).all()
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
                )
            )

        if snapshot:
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

        snapshot = EquitySnapshot(
            timestamp=self.now(), equity_value=account.current_cash + mark_value, cash_balance=account.current_cash
        )
        self._session.add(snapshot)
        self._session.commit()
