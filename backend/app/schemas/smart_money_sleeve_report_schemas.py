from __future__ import annotations

from pydantic import BaseModel


class GroupStatsOut(BaseModel):
    label: str
    n: int
    wins: int
    win_rate: float | None
    win_rate_low: float | None
    win_rate_high: float | None
    avg_r: float | None
    avg_r_low: float | None
    avg_r_high: float | None
    small_sample: bool


class ComparisonOut(BaseModel):
    label: str
    available: bool
    compared: int  # trades that could be priced
    of: int  # closed trades in the sleeve
    reason: str | None  # why some or all were left out
    mean_excess_pct: float | None  # mean (trade return - reference return) per trade, in percentage points
    low: float | None  # bootstrap 95% interval of that mean
    high: float | None
    reference_mean_pct: float | None
    beats: bool | None  # interval entirely above zero


class SleeveReportOut(BaseModel):
    key: str
    name: str
    exists: bool
    closed_trades: int
    min_closed_trades: int
    verdict: str  # not_enough_data | beats_both | beats_one | beats_neither
    candidate_to_disable: bool  # information only
    headline: str
    reason: str | None
    trade_stats: GroupStatsOut
    groups: list[GroupStatsOut]  # by source and by smart-money score band
    vs_spy: ComparisonOut | None
    vs_random: ComparisonOut | None


class SleeveReportResponse(BaseModel):
    sleeves: list[SleeveReportOut]
    caveats: list[str]
    random_draws: int
    random_seed: int
    universe_size: int
