"""Test B helpers (rules were written down before running; kept privately). READ-ONLY study code: pure functions, no I/O.

Membership reconstruction, the SPY 200-day filter, cash months and the per-month return rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from app.catalyst.picker import score_stocks

MARKET_FILTER_BARS = 200
MIN_STOCKS_PER_MONTH = 50
N_BASKETS = 1000
SEED = 7


@dataclass(frozen=True)
class Change:
    day: date  # effective date
    added: str | None
    removed: str | None


def norm_ticker(t: str) -> str:
    return t.replace("|", " ").strip().upper().replace(".", "-")


def members_on(current: set[str], changes: list[Change], day: date) -> set[str]:
    """Members on `day`: the current set with every change effective AFTER `day` undone, newest first."""
    out = set(current)
    for ch in sorted(changes, key=lambda c: c.day, reverse=True):
        if ch.day <= day:
            break
        if ch.added:
            out.discard(ch.added)
        if ch.removed:
            out.add(ch.removed)
    return out


def spy_above_200(spy_closes: np.ndarray, index: int) -> bool | None:
    """SPY close at `index` above the mean of closes[index-199 .. index]; uses nothing after `index`. None if too few bars."""
    if index is None or index + 1 < MARKET_FILTER_BARS:
        return None
    window = spy_closes[index - MARKET_FILTER_BARS + 1 : index + 1]
    return bool(spy_closes[index] > window.mean())


def top_by(values: dict[str, float | None], bar: float) -> list[str]:
    """Symbols whose percentile rank (momentum only, as picker.score_stocks) is >= bar."""
    part = {s: {"momentum": v, "quality": None, "catalyst": None} for s, v in values.items() if v is not None}
    return [x.symbol for x in score_stocks(part, ("momentum",)) if x.score is not None and x.score >= bar]


def month_row(pick_rets: list[float], spy_ret: float, all_arr: np.ndarray, invested: bool, month: date, hold: int) -> dict:
    """One variant-month. A cash month (invested False or no picks) returns 0.0 and has no basket percentile."""
    if not invested or not pick_rets:
        return {"pr": 0.0, "k": 0, "spy": spy_ret, "all": float(all_arr.mean()), "pct": None, "cash": True}
    pr = float(np.mean(pick_rets))
    k = len(pick_rets)
    rng = np.random.default_rng([SEED, month.toordinal(), hold, k])
    idx = np.argsort(rng.random((N_BASKETS, len(all_arr))), axis=1)[:, :k]
    pct = float((all_arr[idx].mean(axis=1) < pr).mean())
    return {"pr": pr, "k": k, "spy": spy_ret, "all": float(all_arr.mean()), "pct": pct, "cash": False}
