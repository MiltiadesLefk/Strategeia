"""Macro data series: the typed result and the catalogue of what is offered.

Two free, keyless public sources feed this: FRED's public CSV download (US
Treasury yields, the curve spread, fed funds, VIX, CPI, unemployment, the
dollar index) and the European Central Bank's data portal (euro-area policy
rates, EUR reference exchange rates, euro-area inflation). Neither is part of
the per-symbol provider chain: they are series, not quotes.

A series is a list of (date, value) points. A missing observation is simply not
a point: a gap is never filled with a zero or a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.data_providers.cache_codec import register_dataclass


@register_dataclass
@dataclass
class MacroSeries:
    """One macro series as fetched."""

    series_id: str  # our catalogue id, e.g. "DGS10" or "ECB_EURUSD"
    name: str
    unit: str
    source: str  # "fred" | "ecb"
    frequency: str  # "daily" | "monthly" | "on change"
    points: list[tuple[date, float]] = field(default_factory=list)
    # When WE fetched it (naive UTC). Not the date of the newest observation:
    # that is `last_observation`. A monthly series fetched today still ends last month.
    last_updated: datetime | None = None

    @property
    def last_observation(self) -> tuple[date, float] | None:
        return self.points[-1] if self.points else None


@dataclass(frozen=True)
class SeriesInfo:
    """One catalogue entry."""

    series_id: str
    name: str
    unit: str
    source: str
    frequency: str
    description: str
    # ECB only: the dataflow and the series key within it.
    ecb_flow: str | None = None
    ecb_key: str | None = None


_FRED = "fred"
_ECB = "ecb"

CATALOGUE: tuple[SeriesInfo, ...] = (
    SeriesInfo("DGS2", "US Treasury 2-year yield", "%", _FRED, "daily", "Constant-maturity 2-year Treasury yield."),
    SeriesInfo("DGS10", "US Treasury 10-year yield", "%", _FRED, "daily", "Constant-maturity 10-year Treasury yield."),
    SeriesInfo("DGS3MO", "US Treasury 3-month yield", "%", _FRED, "daily", "Constant-maturity 3-month Treasury yield."),
    SeriesInfo("T10Y2Y", "10-year minus 2-year spread", "percentage points", _FRED, "daily", "The yield-curve spread; negative means the curve is inverted."),
    SeriesInfo("DFF", "Effective federal funds rate", "%", _FRED, "daily", "The overnight rate the Fed steers."),
    SeriesInfo("VIXCLS", "VIX close", "index points", _FRED, "daily", "CBOE Volatility Index, daily close."),
    SeriesInfo("CPIAUCSL", "US consumer price index", "index 1982-84=100", _FRED, "monthly", "CPI for all urban consumers, seasonally adjusted. An index level, not an inflation rate."),
    SeriesInfo("UNRATE", "US unemployment rate", "%", _FRED, "monthly", "Civilian unemployment rate, seasonally adjusted."),
    SeriesInfo("DTWEXBGS", "Broad US dollar index", "index Jan 2006=100", _FRED, "daily", "Trade-weighted dollar against a broad basket of currencies."),
    SeriesInfo(
        "ECB_DFR", "ECB deposit facility rate", "%", _ECB, "on change",
        "The euro-area policy rate banks earn on overnight deposits. One point per change, not per day.",
        "FM", "B.U2.EUR.4F.KR.DFR.LEV",
    ),
    SeriesInfo(
        "ECB_MRO", "ECB main refinancing rate", "%", _ECB, "on change",
        "The euro-area rate on the central bank's weekly refinancing operations. One point per change, not per day.",
        "FM", "B.U2.EUR.4F.KR.MRR_FR.LEV",
    ),
    SeriesInfo(
        "ECB_EURUSD", "EUR/USD reference rate", "USD per EUR", _ECB, "daily",
        "ECB euro foreign-exchange reference rate (set around 14:15 UTC on working days).",
        "EXR", "D.USD.EUR.SP00.A",
    ),
    SeriesInfo(
        "ECB_EURGBP", "EUR/GBP reference rate", "GBP per EUR", _ECB, "daily",
        "ECB euro foreign-exchange reference rate.", "EXR", "D.GBP.EUR.SP00.A",
    ),
    SeriesInfo(
        "ECB_EURJPY", "EUR/JPY reference rate", "JPY per EUR", _ECB, "daily",
        "ECB euro foreign-exchange reference rate.", "EXR", "D.JPY.EUR.SP00.A",
    ),
    SeriesInfo(
        "ECB_HICP", "Euro-area inflation (HICP, annual rate)", "% year on year", _ECB, "monthly",
        "Headline euro-area consumer inflation, from the ECB's current HICP dataset (the older ICP dataset "
        "stopped at 2025-12).",
        "HICP", "M.U2.N.000000.4D0.ANR",
    ),
)

_BY_ID = {info.series_id: info for info in CATALOGUE}


def catalogue_entry(series_id: str) -> SeriesInfo | None:
    return _BY_ID.get(series_id.strip().upper() if series_id else "")
