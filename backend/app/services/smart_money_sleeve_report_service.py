"""Per-sleeve review report (NEW_PLAN section 8): read-only, informational.

For each sleeve (core, smart-money-auto, smart-money-manual) the closed paper trades are compared with

* SPY over the same dates (entry day close to exit day close, the same direction), and
* random picks from the same universe: for every closed trade, RANDOM_DRAWS symbols drawn at random, held
  over the SAME entry and exit days, the same direction, from a FIXED seed (a pure function of the seed, the
  position id and the draw number, so a re-read gives the same numbers).

Both are reported as the mean excess return per trade with a bootstrap 95% interval, never a bare number.
Below MIN_CLOSED_TRADES closed trades in a sleeve the verdict is `not_enough_data` (n always shown). A
sleeve whose interval does not sit above zero against either reference is flagged `candidate_to_disable`:
information for the person, never read by any gate, never disables anything.

Prices come from the local history store, disk only (`refresh="never"`): a GET makes no network call and
writes nothing. When a price is missing the trade is left out of that comparison and counted; if too few
trades can be compared the verdict degrades to `not_enough_data` with the reason. Nothing is fabricated.
The statistics helpers (bootstrap, Wilson) and the SPY loader are the ones calibration / benchmarks use.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

import pandas as pd
from sqlmodel import Session, select

from app.backtest.benchmarks import BENCHMARK_SYMBOL, BarsLoader, store_loader
from app.portfolio.models import PaperPosition, Sleeve, TradePlanRecord
from app.portfolio.signal_stats import bootstrap_mean_interval, wilson_interval
from app.portfolio.sleeves import CORE_SLEEVE_KEY
from app.schemas.smart_money_sleeve_report_schemas import (
    ComparisonOut,
    GroupStatsOut,
    SleeveReportOut,
    SleeveReportResponse,
)
from app.smartmoney.pipeline_rules import AUTO_SLEEVE_KEY, MANUAL_SLEEVE_KEY

MIN_CLOSED_TRADES = 100  # per sleeve; below it the verdict is not_enough_data
VERDICT_NOT_ENOUGH_DATA = "not_enough_data"
VERDICT_BEATS_BOTH = "beats_both"
VERDICT_BEATS_ONE = "beats_one"
VERDICT_BEATS_NEITHER = "beats_neither"
RANDOM_DRAWS = 20  # random picks per closed trade
RANDOM_SEED = 20261011  # fixed: the report must not change between reads
LOOKBACK_DAYS = 10  # to find the last close at or before a day
SLEEVE_KEYS = (CORE_SLEEVE_KEY, AUTO_SLEEVE_KEY, MANUAL_SLEEVE_KEY)
SCORE_BANDS = ((3.0, 5.0, "score 3-5"), (5.0, 8.0, "score 5-8"), (8.0, float("inf"), "score 8+"))

CAVEATS = [
    "Reported, not proven: this compares what the paper trades did with two simple references. It does not show the signal works.",
    "Returns are price returns from daily closes (entry day close to exit day close), without costs.",
    "The random picks come from today's universe, which only holds survivors, so they are optimistic.",
    f"Below {MIN_CLOSED_TRADES} closed trades a sleeve says nothing; an interval that includes 0 means no difference could be seen.",
    "candidate_to_disable is information only: nothing reads it and nothing is disabled automatically.",
]


def _draw_index(position_id: int, k: int, size: int) -> int:
    digest = hashlib.blake2b(f"{RANDOM_SEED}|{position_id}|{k}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % size


@dataclass
class _Prices:
    """Daily closes per symbol, loaded lazily from the disk-only loader over one span."""

    loader: BarsLoader
    start: date
    end: date
    _closes: dict[str, tuple[list[date], list[float]] | None] = field(default_factory=dict)

    def _load(self, symbol: str) -> tuple[list[date], list[float]] | None:
        if symbol not in self._closes:
            try:
                frame = self.loader(symbol, self.start, self.end)
            except Exception:  # noqa: BLE001 - a symbol without stored bars is simply not comparable
                frame = None
            if frame is None or frame.empty:
                self._closes[symbol] = None
            else:
                days = [pd.Timestamp(d).date() for d in frame["date"]]
                self._closes[symbol] = (days, [float(c) for c in frame["close"]])
        return self._closes[symbol]

    def close_at_or_before(self, symbol: str, day: date) -> float | None:
        data = self._load(symbol)
        if data is None:
            return None
        best: float | None = None
        for d, c in zip(*data):
            if d > day:
                break
            if (day - d).days <= LOOKBACK_DAYS:
                best = c
        return best

    def move_pct(self, symbol: str, entry: date, exit_: date, sign: int) -> float | None:
        a = self.close_at_or_before(symbol, entry)
        b = self.close_at_or_before(symbol, exit_)
        if a is None or b is None or a <= 0:
            return None
        return sign * (b / a - 1.0) * 100.0


def _interval_text(low: float | None, high: float | None) -> str:
    return "no interval" if low is None or high is None else f"{low:+.2f} to {high:+.2f} points"


def _comparison(label: str, excess: list[float], n: int, reference_mean: float | None, reason: str | None) -> ComparisonOut:
    if not excess:
        return ComparisonOut(
            label=label, available=False, compared=0, of=n, reason=reason or "no trade could be compared",
            mean_excess_pct=None, low=None, high=None, reference_mean_pct=None, beats=None,
        )
    interval = bootstrap_mean_interval(excess)
    low, high = interval if interval else (None, None)
    return ComparisonOut(
        label=label,
        available=True,
        compared=len(excess),
        of=n,
        reason=reason,
        mean_excess_pct=sum(excess) / len(excess),
        low=low,
        high=high,
        reference_mean_pct=reference_mean,
        beats=bool(low is not None and low > 0),
    )


def _group(label: str, rs: list[float]) -> GroupStatsOut:
    wins = sum(1 for r in rs if r > 0)
    wilson = wilson_interval(wins, len(rs)) if rs else None
    interval = bootstrap_mean_interval(rs) if rs else None
    return GroupStatsOut(
        label=label,
        n=len(rs),
        wins=wins,
        win_rate=wins / len(rs) * 100 if rs else None,
        win_rate_low=wilson[0] * 100 if wilson else None,
        win_rate_high=wilson[1] * 100 if wilson else None,
        avg_r=sum(rs) / len(rs) if rs else None,
        avg_r_low=interval[0] if interval else None,
        avg_r_high=interval[1] if interval else None,
        small_sample=len(rs) < MIN_CLOSED_TRADES,
    )


def _closed_for(session: Session, sleeve: Sleeve | None, key: str) -> list[PaperPosition]:
    column = PaperPosition.sleeve_id
    if key == CORE_SLEEVE_KEY:
        cond = column.is_(None) if sleeve is None else ((column == sleeve.id) | column.is_(None))
    elif sleeve is None:
        return []
    else:
        cond = column == sleeve.id
    return list(session.exec(select(PaperPosition).where(PaperPosition.status == "closed", cond)).all())


def _day(value: datetime | None) -> date | None:
    return value.date() if value else None


def _one_sleeve(
    session: Session,
    key: str,
    name: str,
    exists: bool,
    positions: list[PaperPosition],
    universe: list[str],
    loader: BarsLoader,
) -> SleeveReportOut:
    n = len(positions)
    plan_ids = {x.trade_plan_id for x in positions if x.trade_plan_id}
    plans = (
        {p.id: p for p in session.exec(select(TradePlanRecord).where(TradePlanRecord.id.in_(plan_ids))).all()}
        if plan_ids
        else {}
    )

    rs = [float(p.realized_r) for p in positions if p.realized_r is not None]
    by_source: dict[str, list[float]] = {}
    by_band: dict[str, list[float]] = {}
    for p in positions:
        if p.realized_r is None:
            continue
        plan = plans.get(p.trade_plan_id) if p.trade_plan_id else None
        by_source.setdefault((plan.source if plan and plan.source else "rules"), []).append(float(p.realized_r))
        if plan is not None and plan.smart_money_score is not None:
            for low, high, label in SCORE_BANDS:
                if low <= plan.smart_money_score < high:
                    by_band.setdefault(label, []).append(float(p.realized_r))
    groups = [_group(f"source: {k}", v) for k, v in sorted(by_source.items())] + [
        _group(label, by_band[label]) for _lo, _hi, label in SCORE_BANDS if label in by_band
    ]

    out = SleeveReportOut(
        key=key,
        name=name,
        exists=exists,
        closed_trades=n,
        min_closed_trades=MIN_CLOSED_TRADES,
        verdict=VERDICT_NOT_ENOUGH_DATA,
        candidate_to_disable=False,
        headline="",
        reason=None,
        trade_stats=_group("all closed trades", rs),
        groups=groups,
        vs_spy=None,
        vs_random=None,
    )
    if n < MIN_CLOSED_TRADES:
        out.reason = f"{n} closed trade{'s' if n != 1 else ''}; at least {MIN_CLOSED_TRADES} are needed before this sleeve is compared."
        out.headline = f"Not enough data: {n} of {MIN_CLOSED_TRADES} closed trades."
        return out

    dated = [(p, _day(p.opened_at), _day(p.closed_at)) for p in positions]
    dated = [(p, a, b) for p, a, b in dated if a is not None and b is not None and p.entry_price > 0]
    if not dated:
        out.reason = "The closed trades have no usable dates."
        out.headline = f"Not enough data: {n} closed trades but no usable dates."
        return out
    prices = _Prices(loader, min(a for _p, a, _b in dated) - timedelta(days=LOOKBACK_DAYS), max(b for _p, _a, b in dated))

    spy_excess: list[float] = []
    spy_moves: list[float] = []
    rand_excess: list[float] = []
    rand_means: list[float] = []
    for p, a, b in dated:
        sign = 1 if p.direction == "long" else -1
        own = sign * (float(p.close_price if p.close_price is not None else p.entry_price) / p.entry_price - 1.0) * 100.0
        spy = prices.move_pct(BENCHMARK_SYMBOL, a, b, sign)
        if spy is not None:
            spy_excess.append(own - spy)
            spy_moves.append(spy)
        pool = [s for s in universe if s != p.symbol] or universe
        if pool:
            draws = [
                m
                for k in range(RANDOM_DRAWS)
                if (m := prices.move_pct(pool[_draw_index(p.id or 0, k, len(pool))], a, b, sign)) is not None
            ]
            # Only a trade with at least half its draws priced is compared: a thin handful would be noise.
            if len(draws) >= RANDOM_DRAWS // 2:
                mean = sum(draws) / len(draws)
                rand_excess.append(own - mean)
                rand_means.append(mean)

    spy_cmp = _comparison(
        f"vs {BENCHMARK_SYMBOL} over the same dates",
        spy_excess,
        n,
        sum(spy_moves) / len(spy_moves) if spy_moves else None,
        None if len(spy_excess) == n else f"{n - len(spy_excess)} trade(s) had no stored {BENCHMARK_SYMBOL} bars.",
    )
    rand_cmp = _comparison(
        f"vs random picks ({RANDOM_DRAWS} per trade, fixed seed)",
        rand_excess,
        n,
        sum(rand_means) / len(rand_means) if rand_means else None,
        None if len(rand_excess) == n else f"{n - len(rand_excess)} trade(s) lacked stored bars for enough random picks.",
    )
    out.vs_spy, out.vs_random = spy_cmp, rand_cmp

    if spy_cmp.compared < MIN_CLOSED_TRADES or rand_cmp.compared < MIN_CLOSED_TRADES:
        out.reason = (
            f"Stored price history covers {spy_cmp.compared} of {n} trades for {BENCHMARK_SYMBOL} and {rand_cmp.compared} for the "
            f"random picks; at least {MIN_CLOSED_TRADES} must be comparable. scripts/preload_history.py stores more history."
        )
        out.headline = f"Not enough data: only {min(spy_cmp.compared, rand_cmp.compared)} of {n} trades could be compared."
        return out

    beats = [bool(spy_cmp.beats), bool(rand_cmp.beats)]
    out.verdict = VERDICT_BEATS_BOTH if all(beats) else VERDICT_BEATS_ONE if any(beats) else VERDICT_BEATS_NEITHER
    out.candidate_to_disable = out.verdict == VERDICT_BEATS_NEITHER
    verdict_text = {
        VERDICT_BEATS_BOTH: "Both intervals sit above zero.",
        VERDICT_BEATS_ONE: "Only one interval sits above zero.",
        VERDICT_BEATS_NEITHER: "Neither interval sits above zero: a candidate to disable (your call).",
    }[out.verdict]
    out.headline = (
        f"{n} closed trades. Mean excess per trade vs {BENCHMARK_SYMBOL}: {spy_cmp.mean_excess_pct:+.2f} points "
        f"({_interval_text(spy_cmp.low, spy_cmp.high)}); vs random picks: {rand_cmp.mean_excess_pct:+.2f} points "
        f"({_interval_text(rand_cmp.low, rand_cmp.high)}). {verdict_text}"
    )
    return out


def build_sleeve_report(
    session: Session, *, loader: BarsLoader | None = None, universe: Callable[[], list[str]] | None = None
) -> SleeveReportResponse:
    """Read-only. Nothing is created, fetched from the network or written."""
    if loader is None:
        from app.data_providers.history_store import get_history_store

        loader = store_loader(get_history_store())
    if universe is None:
        from app.data_providers.universe import load_universe

        def universe() -> list[str]:
            return [e.symbol for e in load_universe()]

    symbols = universe()
    sleeves = {s.key: s for s in session.exec(select(Sleeve)).all()}
    out: list[SleeveReportOut] = []
    for key in SLEEVE_KEYS:
        sleeve = sleeves.get(key)
        out.append(
            _one_sleeve(
                session, key, sleeve.name if sleeve else key, sleeve is not None or key == CORE_SLEEVE_KEY,
                _closed_for(session, sleeve, key), symbols, loader,
            )
        )
    return SleeveReportResponse(
        sleeves=out, caveats=CAVEATS, random_draws=RANDOM_DRAWS, random_seed=RANDOM_SEED, universe_size=len(symbols)
    )
