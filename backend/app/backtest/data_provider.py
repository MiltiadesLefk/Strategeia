"""The data the backtest sees: local price history, cut off at the simulated moment.

`BacktestDataProvider` implements the same DataProvider interface the live app
uses, so generate_trade_plan and the paper-trading engine run on it unchanged.
Everything it returns is decided by ONE thing: the simulated moment T set by
`with as_of(T):` (app.knowledge.point_in_time). There is no way to ask it for a
date.

What is visible at T
--------------------
* Daily bars: only bars that are final at T (markets.is_daily_bar_final). At the
  decision moment of day t (09:45 ET) that is the bars through t-1; after t's
  close (16:30 ET) it is the bars through t. `get_ohlcv` never returns a bar
  dated after the last final one.
* Weekly bars (interval "1wk"): resampled from those same visible daily bars,
  never from the full history. The week that is still in progress is included as
  a PARTIAL bar built only from the days already known (its close is the latest
  known close), which is how a live weekly chart looks mid-week. A week whose
  last trading day is already visible is a complete bar. The weekly rows older
  than the in-progress week are precomputed once per symbol.
* The quote is what a trader sees at T. During the session (the decision
  moment): the price is the day's OPEN (the only part of the day's bar that
  exists at 09:45), the daily change is against the previous close, and the
  volume is 0 because the day's volume has barely started (live reads a
  15-minute partial volume at that time, which is equally useless against a 20-day
  average; the "volume spike" point therefore cannot be earned at the decision
  moment, in the backtest or live). After the close: the price is the day's
  close, with that day's volume. `avg_volume_20d` is the mean of the last 20
  visible bars either way.
* A request that would show a bar after T is refused with LookAheadError, and so
  is any use of the provider outside an `as_of` block.

What it does not know
---------------------
Company overview, financials, news, earnings dates and history, options and
insider activity are not in a price history. They fail exactly the way the live
composite provider does when every source fails (raise AllProvidersFailedError for
overview/financials/news; None or [] for the rest), so every scorer that depends
on them contributes 0 points: this is the price-only core.

Two things beyond prices can be rebuilt for a past date, each switched on per run:
* `overview_from_prices` (the fundamentals switch): the company overview carries the
  52-week high and low taken from the bars visible at the moment (market cap,
  P/E, revenue and EPS stay None: nothing dated is stored for them).
* `dated_sources` (see backtest/dated_sources.py): revenue history by SEC filing
  date, insider buying by Form 4 acceptance time, the earnings surprise record by
  report day. Any part left out raises/returns "nothing" as above.
The date of the NEXT earnings report is never answered (no source says when it was
announced), so `get_earnings_date` stays None.

Extension seam
--------------
`dated_sources` is where later dated data (SEC filings with their filing dates,
past earnings dates) plugs in WITHOUT touching the engine or the scoring code.
It is any object with some of these methods, each called with the symbol and the
simulated moment, returning what the live provider method would:
`company_overview`, `financials`, `news`, `earnings_date`, `earnings_estimate`,
`earnings_history`, `options_summary`, `insider_activity`. A handler MUST return
only what was public at the moment it is given (read facts with
`facts_known_as_of`, never with include_future); the provider can't check that
for it. A method the object lacks stays unavailable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from app.data_providers.base import (
    AllProvidersFailedError,
    CompanyOverview,
    EarningsEstimate,
    EarningsHistoryEntry,
    FinancialsData,
    InsiderActivity,
    NewsItem,
    OptionsSummary,
    QuoteData,
)
from app.data_providers.history_store import HistoryStore, latest_final_day
from app.knowledge.point_in_time import LookAheadError, current_as_of, is_simulated
from app.markets import is_always_on, to_market_time, us_session_bounds

logger = logging.getLogger(__name__)

# How far before the first simulated day the history is loaded. The longest
# lookback the strategy asks for is the weekly chart's "2y"; the extra margin
# covers weekends and holidays at its edge.
HISTORY_LOOKBACK_DAYS = 800

OHLCV_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
# The 52-week range looks back this many calendar days, and needs at least this
# many bars inside the window (about 80% of a year) to count as a 52-week range.
WEEK52_WINDOW_DAYS = 365
MIN_BARS_FOR_52_WEEK_RANGE = 200
VOLUME_AVERAGE_BARS = 20


def _period_start(anchor: date, period: str) -> date | None:
    """First calendar date a provider period ("1mo", "1y", "2y") reaches back to
    from `anchor`; None for "max"."""
    if period == "max":
        return None
    unit = "mo" if period.endswith("mo") else period[-1]
    try:
        amount = int(period[: -len(unit)])
    except ValueError as exc:
        raise AllProvidersFailedError(f"unsupported period {period!r}") from exc
    if unit == "mo":
        return (pd.Timestamp(anchor) - pd.DateOffset(months=amount)).date()
    if unit == "y":
        return (pd.Timestamp(anchor) - pd.DateOffset(years=amount)).date()
    raise AllProvidersFailedError(f"unsupported period {period!r}")


def _is_bar_count_period(period: str) -> bool:
    """"5d" means the last five trading days (not calendar days), as at yfinance."""
    return period.endswith("d") and period[:-1].isdigit()


@dataclass
class SymbolSeries:
    """One symbol's full daily history as numpy columns, plus its weekly rows."""

    symbol: str
    frame: pd.DataFrame
    days: np.ndarray = field(init=False, repr=False)  # datetime64[D] per daily bar
    opens: np.ndarray = field(init=False, repr=False)
    highs: np.ndarray = field(init=False, repr=False)
    lows: np.ndarray = field(init=False, repr=False)
    closes: np.ndarray = field(init=False, repr=False)
    volumes: np.ndarray = field(init=False, repr=False)
    volume_avg20: np.ndarray = field(init=False, repr=False)
    # Weekly rows: one per Monday-start week holding at least one daily bar.
    week_of_bar: np.ndarray = field(init=False, repr=False)  # weekly row of each daily bar
    week_first_bar: np.ndarray = field(init=False, repr=False)  # first daily-bar index of each week
    week_last_bar: np.ndarray = field(init=False, repr=False)
    week_start: np.ndarray = field(init=False, repr=False)  # datetime64[D], the Monday
    week_open: np.ndarray = field(init=False, repr=False)
    week_high: np.ndarray = field(init=False, repr=False)
    week_low: np.ndarray = field(init=False, repr=False)
    week_close: np.ndarray = field(init=False, repr=False)
    week_volume: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        frame = self.frame
        self.days = frame["date"].to_numpy().astype("datetime64[D]")
        self.opens = frame["open"].to_numpy(dtype="float64")
        self.highs = frame["high"].to_numpy(dtype="float64")
        self.lows = frame["low"].to_numpy(dtype="float64")
        self.closes = frame["close"].to_numpy(dtype="float64")
        self.volumes = frame["volume"].to_numpy(dtype="float64")
        self.volume_avg20 = (
            pd.Series(self.volumes).rolling(VOLUME_AVERAGE_BARS, min_periods=1).mean().to_numpy(dtype="float64")
        )
        # 1970-01-01 was a Thursday, so (days since epoch + 3) % 7 is Monday=0.
        epoch_days = self.days.astype("int64")
        monday = self.days - ((epoch_days + 3) % 7).astype("timedelta64[D]")
        starts, first_index, inverse = np.unique(monday, return_index=True, return_inverse=True)
        self.week_start = starts
        self.week_of_bar = inverse.reshape(-1)
        self.week_first_bar = first_index
        last = np.full(len(starts), -1, dtype="int64")
        np.maximum.at(last, self.week_of_bar, np.arange(len(self.days)))
        self.week_last_bar = last
        self.week_open = self.opens[first_index]
        self.week_close = self.closes[last]
        self.week_high = np.maximum.reduceat(self.highs, first_index)
        self.week_low = np.minimum.reduceat(self.lows, first_index)
        self.week_volume = np.add.reduceat(np.nan_to_num(self.volumes), first_index)

    def __len__(self) -> int:
        return len(self.days)

    def bar_index_on(self, day: date) -> int | None:
        """Index of the bar dated `day`, or None."""
        target = np.datetime64(day, "D")
        i = int(np.searchsorted(self.days, target, side="left"))
        return i if i < len(self.days) and self.days[i] == target else None


class PriceBook:
    """Daily history of every symbol a run uses, loaded once and shared."""

    def __init__(self, series: dict[str, SymbolSeries], skipped: dict[str, str] | None = None):
        self.series = series
        self.skipped = dict(skipped or {})

    def __contains__(self, symbol: str) -> bool:
        return symbol in self.series

    @classmethod
    def from_frames(cls, frames: dict[str, pd.DataFrame]) -> "PriceBook":
        series: dict[str, SymbolSeries] = {}
        skipped: dict[str, str] = {}
        for symbol, frame in frames.items():
            if frame is None or frame.empty:
                skipped[symbol] = "no stored price history"
                continue
            series[symbol] = SymbolSeries(symbol, frame[OHLCV_COLUMNS].reset_index(drop=True))
        return cls(series, skipped)

    @classmethod
    def load(cls, store: HistoryStore, symbols: list[str], start: date, end: date) -> "PriceBook":
        """Read `symbols` from the local history store (disk only, no network),
        from HISTORY_LOOKBACK_DAYS before `start` through `end`."""
        load_from = start - timedelta(days=HISTORY_LOOKBACK_DAYS)
        frames = {
            symbol: store.get_daily_history(symbol, start=load_from, end=end, refresh="never") for symbol in symbols
        }
        return cls.from_frames(frames)


@dataclass
class ProvidedCall:
    """One answered request, kept when `record_calls` is on (look-ahead probe)."""

    method: str
    symbol: str
    as_of: datetime
    last_bar_day: date | None
    rows: int


class BacktestDataProvider:
    """See the module docstring."""

    name = "backtest"

    def __init__(
        self,
        book: PriceBook,
        *,
        dated_sources: Any | None = None,
        record_calls: bool = False,
        overview_from_prices: bool = False,
    ):
        self._book = book
        self._sources = dated_sources
        self._overview_from_prices = overview_from_prices
        self.record_calls = record_calls
        self.calls: list[ProvidedCall] = []
        self._cutoff_for: tuple[datetime, date] | None = None

    @property
    def dated_sources(self) -> Any | None:
        return self._sources

    # ---- the as-of rule ----------------------------------------------------

    def _moment(self) -> datetime:
        if not is_simulated():
            raise LookAheadError(
                "BacktestDataProvider only answers inside `with as_of(moment):`; without a simulated "
                "moment there is no way to know which bars are in the past"
            )
        return current_as_of()

    def _last_final_day(self, moment: datetime) -> date:
        """Date of the newest daily bar that is final at `moment`."""
        cached = self._cutoff_for
        if cached is not None and cached[0] == moment:
            return cached[1]
        day = latest_final_day("SPY", moment)  # every symbol here is a US equity
        self._cutoff_for = (moment, day)
        return day

    def _visible(self, series: SymbolSeries, moment: datetime) -> int:
        """How many of the series' bars are visible at `moment`."""
        cutoff = np.datetime64(self._last_final_day(moment), "D")
        return int(np.searchsorted(series.days, cutoff, side="right"))

    def _series(self, symbol: str) -> SymbolSeries:
        if is_always_on(symbol):
            raise AllProvidersFailedError(f"the backtest covers US equities only, not {symbol}")
        series = self._book.series.get(symbol)
        if series is None:
            raise AllProvidersFailedError(f"no stored price history for {symbol}")
        return series

    def _guard(self, series: SymbolSeries, visible: int, moment: datetime) -> None:
        """Last line of defence: refuse to hand out a bar that is not final at `moment`."""
        if visible and series.days[visible - 1].astype(object) > self._last_final_day(moment):
            raise LookAheadError(f"{series.symbol}: bar {series.days[visible - 1]} is not final at {moment}")

    def _log(self, method: str, symbol: str, moment: datetime, last_day: date | None, rows: int) -> None:
        if self.record_calls:
            self.calls.append(ProvidedCall(method, symbol, moment, last_day, rows))

    # ---- prices ------------------------------------------------------------

    def get_ohlcv(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        moment = self._moment()
        series = self._series(symbol)
        if interval not in ("1d", "1wk"):
            # Daily history only: the answer a live provider gives when it has nothing for the request.
            raise AllProvidersFailedError(f"the backtest has daily bars only, not interval {interval!r}")
        visible = self._visible(series, moment)
        if visible == 0:
            raise AllProvidersFailedError(f"no {symbol} bars are known yet at {moment}")
        self._guard(series, visible, moment)
        anchor = to_market_time(moment).date()
        if interval == "1d":
            frame = self._daily(series, visible, period, anchor)
        else:
            frame = self._weekly(series, visible, period, anchor)
        if frame.empty:
            raise AllProvidersFailedError(f"no {symbol} bars in period {period} at {moment}")
        self._log("get_ohlcv", symbol, moment, pd.Timestamp(frame["date"].iloc[-1]).date(), len(frame))
        return frame

    @staticmethod
    def _daily(series: SymbolSeries, visible: int, period: str, anchor: date) -> pd.DataFrame:
        if _is_bar_count_period(period):
            low = max(0, visible - int(period[:-1]))
        else:
            start = _period_start(anchor, period)
            low = 0 if start is None else int(np.searchsorted(series.days, np.datetime64(start, "D"), side="left"))
        return series.frame.iloc[min(low, visible):visible]

    @staticmethod
    def _weekly(series: SymbolSeries, visible: int, period: str, anchor: date) -> pd.DataFrame:
        if _is_bar_count_period(period):
            raise AllProvidersFailedError(f"day periods are not supported for weekly bars ({period!r})")
        last_bar = visible - 1
        week = int(series.week_of_bar[last_bar])
        complete = bool(series.week_last_bar[week] == last_bar)
        start = _period_start(anchor, period)
        low = 0 if start is None else int(np.searchsorted(series.week_start, np.datetime64(start, "D"), side="left"))
        low = min(low, week)
        columns = {
            "date": series.week_start[low : week + 1].astype("datetime64[us]"),
            "open": series.week_open[low : week + 1].copy(),
            "high": series.week_high[low : week + 1].copy(),
            "low": series.week_low[low : week + 1].copy(),
            "close": series.week_close[low : week + 1].copy(),
            "volume": series.week_volume[low : week + 1].copy(),
        }
        if not complete:
            # The week is still in progress: rebuild its last row from the visible days only
            # (the precomputed row was built from the whole week, i.e. from the future).
            first = int(series.week_first_bar[week])
            columns["open"][-1] = series.opens[first]
            columns["high"][-1] = series.highs[first:visible].max()
            columns["low"][-1] = series.lows[first:visible].min()
            columns["close"][-1] = series.closes[last_bar]
            columns["volume"][-1] = np.nansum(series.volumes[first:visible])
        return pd.DataFrame(columns)

    def get_quote(self, symbol: str) -> QuoteData:
        moment = self._moment()
        series = self._series(symbol)
        visible = self._visible(series, moment)
        if visible < 2:
            raise AllProvidersFailedError(f"not enough {symbol} bars known at {moment} for a quote")
        self._guard(series, visible, moment)
        now_et = to_market_time(moment)
        today = now_et.date()
        bounds = us_session_bounds(today)
        last_close = float(series.closes[visible - 1])
        avg_volume = float(series.volume_avg20[visible - 1])
        # Mid-session: today's bar exists but is not final, and all of it that a
        # trader can know is the opening print.
        in_session = bounds is not None and now_et >= bounds.open and self._last_final_day(moment) < today
        opening_bar = series.bar_index_on(today) if in_session else None
        if opening_bar is not None and opening_bar == visible:
            price = float(series.opens[opening_bar])
            change_pct = (price - last_close) / last_close * 100 if last_close else 0.0
            volume = 0.0
        else:
            price = last_close
            previous = float(series.closes[visible - 2])
            change_pct = (price - previous) / previous * 100 if previous else 0.0
            volume = float(series.volumes[visible - 1])
        self._log("get_quote", symbol, moment, series.days[visible - 1].astype(object), 1)
        return QuoteData(symbol=symbol, price=price, change_pct_24h=change_pct, volume=volume, avg_volume_20d=avg_volume)

    # ---- what a price history cannot know ----------------------------------

    def _handler(self, name: str):
        return getattr(self._sources, name, None) if self._sources is not None else None

    def get_company_overview(self, symbol: str) -> CompanyOverview:
        handler = self._handler("company_overview")
        if handler is not None:
            return handler(symbol, self._moment())
        if self._overview_from_prices:
            return self._price_overview(symbol)
        raise AllProvidersFailedError(f"backtest: no dated company overview for {symbol}")

    def _price_overview(self, symbol: str) -> CompanyOverview:
        """The overview a price history can support: the 52-week range over the
        visible bars. The window is the 365 days up to the decision date, and a
        history shorter than MIN_BARS_FOR_52_WEEK_RANGE is refused rather than
        reported as a (too narrow) 52-week range."""
        moment = self._moment()
        series = self._series(symbol)
        visible = self._visible(series, moment)
        self._guard(series, visible, moment)
        anchor = to_market_time(moment).date()
        low = int(np.searchsorted(series.days, np.datetime64(anchor - timedelta(days=WEEK52_WINDOW_DAYS), "D"), side="left"))
        if visible - low < MIN_BARS_FOR_52_WEEK_RANGE:
            raise AllProvidersFailedError(f"backtest: too little {symbol} history to know a 52-week range at {moment}")
        self._log("get_company_overview", symbol, moment, series.days[visible - 1].astype(object), visible - low)
        return CompanyOverview(
            symbol=symbol,
            name=symbol,
            market_cap=None,
            pe_ratio=None,
            revenue_ttm=None,
            eps_ttm=None,
            week52_low=float(series.lows[low:visible].min()),
            week52_high=float(series.highs[low:visible].max()),
        )

    def get_financials(self, symbol: str) -> FinancialsData:
        handler = self._handler("financials")
        if handler is not None:
            return handler(symbol, self._moment())
        raise AllProvidersFailedError(f"backtest: no dated financials for {symbol}")

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        handler = self._handler("news")
        if handler is not None:
            return handler(symbol, self._moment())[:limit]
        raise AllProvidersFailedError(f"backtest: no dated news for {symbol}")

    def get_earnings_date(self, symbol: str) -> date | None:
        handler = self._handler("earnings_date")
        return handler(symbol, self._moment()) if handler is not None else None

    def get_earnings_estimate(self, symbol: str) -> EarningsEstimate | None:
        handler = self._handler("earnings_estimate")
        return handler(symbol, self._moment()) if handler is not None else None

    def get_earnings_history(self, symbol: str, limit: int = 12) -> list[EarningsHistoryEntry]:
        handler = self._handler("earnings_history")
        return handler(symbol, self._moment())[:limit] if handler is not None else []

    def get_options_summary(self, symbol: str) -> OptionsSummary | None:
        handler = self._handler("options_summary")
        return handler(symbol, self._moment()) if handler is not None else None

    def get_insider_activity(self, symbol: str) -> InsiderActivity | None:
        handler = self._handler("insider_activity")
        return handler(symbol, self._moment()) if handler is not None else None
