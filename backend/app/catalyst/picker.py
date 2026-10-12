"""Month-end "picker" scoring (picker test, step 3; rules were written down before running). READ-ONLY study code.

Three parts per stock, each turned into a percentile rank across the stocks that have it, SCORE = the plain
average of the available ranks (equal weights, no tuning). A stock lacking a part is scored on its other parts
and flagged; a missing part is never counted as zero. Everything is read inside `with as_of(close_moment(M))`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlmodel import Session

from app.backtest.calendar import close_moment
from app.backtest.data_provider import SymbolSeries
from app.backtest.fundamentals_history import revenue_history_as_of
from app.catalyst.scoring import (
    ALL_KINDS,
    DEFAULT_KIND_CAP,
    KIND_CONGRESS,
    read_catalysts,
)
from app.knowledge.congress_trades import EQUITY_ASSET_TYPES, congress_trades_as_of
from app.knowledge.point_in_time import as_of

PARTS = ("quality", "momentum", "catalyst")
CATALYST_WINDOW_DAYS = 30
MOMENTUM_SKIP_BARS = 21
MOMENTUM_LOOKBACK_BARS = 126
TOP_N = 6
# The House bracket that is ignored for the catalyst part.
SMALL_BRACKET_LOW = 1001
SMALL_BRACKET_HIGH = 15000


@dataclass
class PickScore:
    symbol: str
    score: float | None  # None: the stock has no part at all
    ranks: dict[str, float] = field(default_factory=dict)
    values: dict[str, float | None] = field(default_factory=dict)
    missing: tuple[str, ...] = ()  # parts this stock lacks (flagged, not scored as zero)


def quality_value(session: Session, symbol: str, moment: datetime) -> float | None:
    """Year-over-year revenue growth from the two newest consecutive fiscal years public at `moment`."""
    years = revenue_history_as_of(session, symbol, moment)
    if len(years) < 2:
        return None
    prev, last = years[-2], years[-1]
    if last.year - prev.year != 1 or prev.revenue <= 0:
        return None
    return last.revenue / prev.revenue - 1


def momentum_value(series: SymbolSeries, index: int) -> float | None:
    """close(M-21 bars) / close(M-126 bars) - 1, prices only."""
    if index is None or index - MOMENTUM_LOOKBACK_BARS < 0:
        return None
    return float(series.closes[index - MOMENTUM_SKIP_BARS] / series.closes[index - MOMENTUM_LOOKBACK_BARS] - 1)


def _congress_net_ignoring_small(session: Session, symbol: str, moment: datetime, window_days: int) -> int:
    trades = congress_trades_as_of(session, symbol, moment, window_days, asset_types=EQUITY_ASSET_TYPES)
    keep = [t for t in trades if not (t.amount_low == SMALL_BRACKET_LOW and t.amount_high == SMALL_BRACKET_HIGH)]
    buyers = {t.member_key for t in keep if t.side == "buy"}
    sellers = {t.member_key for t in keep if t.side == "sell"}
    return len(buyers) - len(sellers)


def catalyst_value(
    session: Session, symbol: str, moment: datetime, kind_cap: int = DEFAULT_KIND_CAP, kinds: tuple[str, ...] = ALL_KINDS
) -> float | None:
    """Net catalyst points over 30 days from `read_catalysts`, with the $1,001-$15,000 House bracket ignored.
    Only the `kinds` listed count. None when none of them had any stored data at all (silence is not the same
    as no data)."""
    reading = read_catalysts(session, symbol, moment, window_days=CATALYST_WINDOW_DAYS, kind_cap=kind_cap)
    if not any(reading.covered.get(k) for k in kinds):
        return None
    net = sum(points for kind, points in reading.by_kind.items() if kind in kinds and kind != KIND_CONGRESS)
    if KIND_CONGRESS in kinds and reading.covered.get(KIND_CONGRESS):
        net += max(-kind_cap, min(kind_cap, _congress_net_ignoring_small(session, symbol, moment, CATALYST_WINDOW_DAYS)))
    return float(net)


def catalyst_by_kind(
    session: Session, symbol: str, moment: datetime, kind_cap: int = DEFAULT_KIND_CAP
) -> dict[str, float | None]:
    """The catalyst value of each kind separately (None = that kind has no stored data), House $1,001-$15,000 ignored."""
    reading = read_catalysts(session, symbol, moment, window_days=CATALYST_WINDOW_DAYS, kind_cap=kind_cap)
    out: dict[str, float | None] = {}
    for kind in ALL_KINDS:
        if not reading.covered.get(kind):
            out[kind] = None
        elif kind == KIND_CONGRESS:
            net = _congress_net_ignoring_small(session, symbol, moment, CATALYST_WINDOW_DAYS)
            out[kind] = float(max(-kind_cap, min(kind_cap, net)))
        else:
            out[kind] = float(reading.by_kind.get(kind, 0))
    return out


def percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    """Rank 0..1 (average rank for ties, so equal values get the same rank). One value -> 0.5."""
    n = len(values)
    if n == 0:
        return {}
    if n == 1:
        return {k: 0.5 for k in values}
    arr = list(values.values())
    out = {}
    for sym, v in values.items():
        less = sum(1 for x in arr if x < v)
        equal = sum(1 for x in arr if x == v)
        out[sym] = (less + (equal - 1) / 2) / (n - 1)
    return out


def score_stocks(
    parts: dict[str, dict[str, float | None]], use: tuple[str, ...] = PARTS
) -> list[PickScore]:
    """`parts[symbol][part]` -> value or None (missing). Only the parts in `use` are scored (the rest count as
    missing). Deterministic: sorted by symbol."""
    ranks = {
        p: percentile_ranks({s: v[p] for s, v in parts.items() if v.get(p) is not None}) if p in use else {}
        for p in PARTS
    }
    out = []
    for sym in sorted(parts):
        got = {p: ranks[p][sym] for p in PARTS if sym in ranks[p]}
        out.append(
            PickScore(
                symbol=sym,
                score=(sum(got.values()) / len(got)) if got else None,
                ranks=got,
                values={p: parts[sym].get(p) for p in PARTS},
                missing=tuple(p for p in PARTS if p not in got),
            )
        )
    return out


def top_picks(scores: list[PickScore], n: int = TOP_N) -> list[PickScore]:
    scored = [s for s in scores if s.score is not None]
    return sorted(scored, key=lambda s: (-s.score, s.symbol))[:n]


def read_parts(
    session: Session, series: dict[str, SymbolSeries], month_end: date, kinds: tuple[str, ...] = ALL_KINDS
) -> dict[str, dict[str, float | None]]:
    """The three raw part values of every stock at the close of `month_end` (facts known then, prices through that day)."""
    moment = close_moment(month_end)
    parts: dict[str, dict[str, float | None]] = {}
    with as_of(moment):
        for sym, s in series.items():
            idx = s.bar_index_on(month_end)
            if idx is None:
                continue
            parts[sym] = {
                "quality": quality_value(session, sym, moment),
                "momentum": momentum_value(s, idx),
                "catalyst": catalyst_value(session, sym, moment, kinds=kinds),
            }
    return parts


def score_month_end(
    session: Session, series: dict[str, SymbolSeries], month_end: date
) -> list[PickScore]:
    """Score every stock at the close of `month_end`, from facts known then and prices through that day."""
    moment = close_moment(month_end)
    parts: dict[str, dict[str, float | None]] = {}
    with as_of(moment):
        for sym, s in series.items():
            idx = s.bar_index_on(month_end)
            if idx is None:
                continue
            parts[sym] = {
                "quality": quality_value(session, sym, moment),
                "momentum": momentum_value(s, idx),
                "catalyst": catalyst_value(session, sym, moment),
            }
    return score_stocks(parts)


__all__ = [
    "PARTS", "PickScore", "TOP_N", "catalyst_by_kind", "catalyst_value", "momentum_value", "percentile_ranks",
    "quality_value", "read_parts", "score_month_end", "score_stocks", "top_picks",
]
