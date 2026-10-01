from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from app.schemas.common import UtcDatetime


class _FromAttributes(BaseModel):
    # The report is built from dataclasses in app.portfolio.missed_trade_report.
    model_config = ConfigDict(from_attributes=True)


class MissedDetailCountSchema(_FromAttributes):
    detail: str
    label: str
    n: int


class MissedCategorySchema(_FromAttributes):
    key: str
    label: str
    n: int
    resolved: int
    open: int
    awaiting: int
    not_simulated: int
    wins: int
    win_rate: float | None
    win_rate_low: float | None
    win_rate_high: float | None
    avg_r: float | None
    avg_r_low: float | None
    avg_r_high: float | None
    total_r: float
    open_avg_r: float | None
    small_sample: bool
    details: list[MissedDetailCountSchema]


class MissedTakenSchema(_FromAttributes):
    n: int
    wins: int
    win_rate: float | None
    win_rate_low: float | None
    win_rate_high: float | None
    avg_r: float | None
    avg_r_low: float | None
    avg_r_high: float | None
    total_r: float
    small_sample: bool


class MissedQuestionSchema(_FromAttributes):
    key: str
    question: str
    verdict: str
    answer: str


class MissedTradeItemSchema(_FromAttributes):
    plan_id: int
    symbol: str
    created_at: UtcDatetime
    category: str
    category_label: str
    detail: str | None
    detail_label: str | None
    reason: str | None
    state: str
    direction: str | None
    confidence_score: int
    entry: float | None
    stop: float | None
    tp1: float | None
    r_multiple: float | None
    exit_reason: str | None
    exit_date: date | None
    trade_source: str | None
    note: str | None


class MissedTradeReportSchema(_FromAttributes):
    plans_considered: int
    awaiting_refresh: int
    min_trades_for_reading: int
    last_computed_at: UtcDatetime | None
    categories: list[MissedCategorySchema]
    taken: MissedTakenSchema
    questions: list[MissedQuestionSchema]
    trades: list[MissedTradeItemSchema]
    trades_listed: int
    caveats: list[str]


class MissedTradeRefreshSchema(_FromAttributes):
    considered: int
    computed: int
    resolved: int
    still_open: int
    no_data: int
    not_simulatable: int
    skipped_resolved: int
    remaining: int
    busy: bool
    failed_symbols: list[str]
