from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _FromAttributes(BaseModel):
    # The report is built from dataclasses in app.portfolio.calibration.
    model_config = ConfigDict(from_attributes=True)


class CalibrationBandSchema(_FromAttributes):
    label: str
    min_points: int
    max_points: int
    n: int
    wins: int
    win_rate: float | None
    win_rate_low: float | None
    win_rate_high: float | None
    avg_r: float | None
    avg_r_low: float | None
    avg_r_high: float | None
    total_pnl: float
    small_sample: bool


class IcSchema(_FromAttributes):
    key: str
    label: str
    n: int
    ic: float | None
    p_value: float | None
    p_value_adjusted: float | None = None
    ci_low: float | None
    ci_high: float | None
    verdict: str
    note: str
    n_nonzero: int | None = None


class CalibrationExclusionsSchema(_FromAttributes):
    no_linked_plan: int
    missing_r: int
    outside_bands: int
    total: int


class CalibrationReportSchema(_FromAttributes):
    closed_trades: int
    analyzed_trades: int
    excluded: CalibrationExclusionsSchema
    points_max: int
    min_trades_for_reading: int
    min_trades_per_band: int
    reliable: bool
    headline: str
    bands: list[CalibrationBandSchema]
    overall: CalibrationBandSchema | None
    ic: IcSchema
    ic_by_direction: list[IcSchema]
    components: list[IcSchema]
    components_tested: int
    notes: list[str]
