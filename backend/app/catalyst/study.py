"""Measurement of the catalyst idea on past days (plan.md section 8, step 1).

A STUDY, not the trading engine: it asks "on the days the catalyst net reached the trigger, how did a trade
with a chart-independent stop and a time limit do?" It reads only facts public at 09:45 ET of each day
(`with as_of(...)`), enters at that day's open and walks the daily bars. Simplifications, all stated in the
report: daily bars only (stop before target within a bar, a gap fills at the open), long only, no cash or
position limits, small fixed slippage. The live sleeve, if built, trades through the paper engine; this file
never sizes a real position.

The same exit rules are applied to randomly chosen entry days of the same symbols, so a number can be read
against "any day would have done this well too".
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlmodel import Session

from app.backtest.calendar import decision_moment, trading_days
from app.backtest.data_provider import PriceBook, SymbolSeries
from app.catalyst.scoring import ALL_KINDS, DEFAULT_KIND_CAP, DEFAULT_WINDOW_DAYS, read_catalysts
from app.knowledge.point_in_time import as_of

ATR_PERIOD = 14
ATR_STOP_MULTIPLE = 1.5
MIN_STOP_ATR = 1.0  # a stop closer than this is inside daily noise
SUPPORT_BARS = 10
TARGET_R = 2.0
MAX_HOLD_BARS = 10
SLIPPAGE = 0.0005  # per side, as a fraction of price
WARMUP_BARS = 30
JUMP_PCT = 5.0  # "the stock jumped": best high within the hold is this far above the entry
BASELINE_PER_TRIGGER = 20


@dataclass(frozen=True)
class StudyParams:
    window_days: int = DEFAULT_WINDOW_DAYS
    trigger: int = 3
    kind_cap: int = DEFAULT_KIND_CAP
    cooldown_bars: int = MAX_HOLD_BARS  # no second trigger on a symbol while its trade could still be open
    seed: int = 7


@dataclass
class SimTrade:
    symbol: str
    day: date
    entry: float
    stop: float
    target: float
    exit_day: date
    exit_price: float
    reason: str  # stop | target | time | data_end
    r: float
    best_pct: float  # best high over the hold, percent above entry
    net: int = 0
    detail: str = ""


def _atr(series: SymbolSeries, index: int) -> float | None:
    """ATR over the ATR_PERIOD bars before `index` (the entry day's own bar is not known at 09:45)."""
    if index < ATR_PERIOD + 1:
        return None
    hi = series.highs[index - ATR_PERIOD : index]
    lo = series.lows[index - ATR_PERIOD : index]
    prev = series.closes[index - ATR_PERIOD - 1 : index - 1]
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - prev), np.abs(lo - prev)))
    value = float(np.mean(tr))
    return value if value > 0 else None


def plan_levels(series: SymbolSeries, index: int, entry: float) -> tuple[float, float] | None:
    """(stop, target) for a long entered at `entry` on bar `index`, from what was known before the open.

    The stop is the closer of an ATR stop and the lowest low of the last SUPPORT_BARS bars (when it is below
    the entry), never closer than MIN_STOP_ATR. The target is TARGET_R times the risk."""
    atr = _atr(series, index)
    if atr is None:
        return None
    candidates = [entry - ATR_STOP_MULTIPLE * atr]
    support = float(np.min(series.lows[max(0, index - SUPPORT_BARS) : index]))
    if support < entry:
        candidates.append(support)
    stop = max(candidates)  # the closer one
    stop = min(stop, entry - MIN_STOP_ATR * atr)
    risk = entry - stop
    return (stop, entry + TARGET_R * risk) if risk > 0 else None


def simulate_long(series: SymbolSeries, index: int, stop: float, target: float, entry: float) -> SimTrade | None:
    """Walk the bars from the entry day (entered at its open) to the exit. Stop is checked before the
    target inside one bar (daily bars cannot order them); a bar opening beyond a level fills at its open."""
    n = len(series)
    fill = entry * (1 + SLIPPAGE)
    risk = fill - stop
    if risk <= 0:
        return None
    best_high = entry
    last = min(index + MAX_HOLD_BARS, n - 1)
    for i in range(index, last + 1):
        o, h, l, c = series.opens[i], series.highs[i], series.lows[i], series.closes[i]
        best_high = max(best_high, h)
        if i > index and o <= stop:
            price, reason = o, "stop"
        elif l <= stop:
            price, reason = stop, "stop"
        elif i > index and o >= target:
            price, reason = o, "target"
        elif h >= target:
            price, reason = target, "target"
        elif i == index + MAX_HOLD_BARS:
            price, reason = c, "time"
        elif i == n - 1:
            price, reason = c, "data_end"
        else:
            continue
        exit_price = price * (1 - SLIPPAGE)
        return SimTrade(
            symbol=series.symbol, day=series.days[index].astype("datetime64[D]").astype(object), entry=fill,
            stop=stop, target=target, exit_day=series.days[i].astype("datetime64[D]").astype(object),
            exit_price=exit_price, reason=reason, r=(exit_price - fill) / risk,
            best_pct=(best_high / entry - 1) * 100,
        )
    return None


@dataclass
class Summary:
    n: int
    win_rate: float | None
    avg_r: float | None
    median_r: float | None
    r_low: float | None  # 95% interval of the mean
    r_high: float | None
    jump_rate: float | None
    exits: dict[str, int]


def summarize(trades: list[SimTrade]) -> Summary:
    if not trades:
        return Summary(0, None, None, None, None, None, None, {})
    rs = np.array([t.r for t in trades])
    n = len(rs)
    mean = float(rs.mean())
    half = 1.96 * float(rs.std(ddof=1)) / math.sqrt(n) if n > 1 else None
    exits: dict[str, int] = {}
    for t in trades:
        exits[t.reason] = exits.get(t.reason, 0) + 1
    return Summary(
        n=n, win_rate=float((rs > 0).mean()), avg_r=mean, median_r=float(np.median(rs)),
        r_low=None if half is None else mean - half, r_high=None if half is None else mean + half,
        jump_rate=float(np.mean([t.best_pct >= JUMP_PCT for t in trades])), exits=exits,
    )


@dataclass
class StudyResult:
    params: StudyParams
    start: date
    end: date
    symbols: list[str]
    days_evaluated: int = 0
    triggers: list[SimTrade] = field(default_factory=list)
    baseline: list[SimTrade] = field(default_factory=list)
    coverage: dict[str, int] = field(default_factory=dict)  # kind -> symbols that had any stored data
    skipped: dict[str, str] = field(default_factory=dict)
    kind_counts: dict[str, int] = field(default_factory=dict)  # kind -> trigger days it contributed to
    events: list[dict] = field(default_factory=list)  # every day with any catalyst: the items and what followed


def _event(series: SymbolSeries, index: int, reading) -> dict:
    """One day with catalysts: the exact items and the price afterwards (open of that day -> later closes)."""
    entry = float(series.opens[index])
    after = {}
    for n in (1, 5, 10):
        j = index + n - 1
        after[n] = (float(series.closes[j]) / entry - 1) * 100 if j < len(series) else None
    return {"symbol": series.symbol, "day": series.days[index].astype(object), "net": reading.net,
            "items": list(reading.items), "by_kind": dict(reading.by_kind), "ret": after}


def run_study(
    session: Session, book: PriceBook, symbols: list[str], start: date, end: date, params: StudyParams | None = None
) -> StudyResult:
    params = params or StudyParams()
    result = StudyResult(params=params, start=start, end=end, symbols=[s for s in symbols if s in book])
    result.skipped = {s: "no stored price history" for s in symbols if s not in book}
    days = trading_days(start, end)
    rng = random.Random(params.seed)
    covered_symbols = {k: set() for k in ALL_KINDS}

    for symbol in result.symbols:
        series = book.series[symbol]
        blocked_until = -1
        eligible: list[int] = []
        for day in days:
            index = series.bar_index_on(day)
            if index is None or index < WARMUP_BARS:
                continue
            eligible.append(index)
            result.days_evaluated += 1
            with as_of(decision_moment(day)):
                reading = read_catalysts(
                    session, symbol, decision_moment(day), window_days=params.window_days, kind_cap=params.kind_cap
                )
            for kind, ok in reading.covered.items():
                if ok:
                    covered_symbols[kind].add(symbol)
            if reading.items:
                result.events.append(_event(series, index, reading))
            if index <= blocked_until or reading.net < params.trigger:
                continue
            entry = float(series.opens[index])
            levels = plan_levels(series, index, entry)
            if levels is None:
                continue
            trade = simulate_long(series, index, levels[0], levels[1], entry)
            if trade is None:
                continue
            trade.net = reading.net
            trade.detail = "; ".join(f"{i.text}" for i in reading.items)
            result.triggers.append(trade)
            for kind in reading.by_kind:
                result.kind_counts[kind] = result.kind_counts.get(kind, 0) + 1
            blocked_until = index + params.cooldown_bars

        # The same exits on random days of the same symbol: what an ordinary day would have done.
        want = BASELINE_PER_TRIGGER * sum(1 for t in result.triggers if t.symbol == symbol)
        pool = [i for i in eligible]
        rng.shuffle(pool)
        for index in pool[:want]:
            entry = float(series.opens[index])
            levels = plan_levels(series, index, entry)
            trade = simulate_long(series, index, *levels, entry) if levels else None
            if trade is not None:
                result.baseline.append(trade)

    result.coverage = {k: len(v) for k, v in covered_symbols.items()}
    return result
