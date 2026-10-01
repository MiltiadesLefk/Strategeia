"""One entry point for macro series: look the id up in the catalogue, ask the
right source, return a MacroSeries with the catalogue's name and unit.

    get_macro_series("DGS10")                     # FRED, last few years
    get_macro_series("ECB_EURUSD", start=date(2026, 1, 1))

Never fabricates: an unknown id is `UnknownSeriesError`, a failed fetch is a
`DataProviderError` (the cache serves the last good copy first when it has one).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from app.data_providers.base import DataProviderError
from app.data_providers.ecb_provider import EcbProvider
from app.data_providers.fred_provider import FredProvider
from app.data_providers.macro_series import CATALOGUE, MacroSeries, SeriesInfo, catalogue_entry

# FRED series start this many calendar years back when the caller gives no
# start. Anchored to 1 January so the cache key is stable all year.
DEFAULT_LOOKBACK_YEARS = 5

_fred = FredProvider()
_ecb = EcbProvider()


class UnknownSeriesError(DataProviderError):
    """The id is not in the catalogue."""


def list_series() -> tuple[SeriesInfo, ...]:
    return CATALOGUE


def get_macro_series(
    series_id: str,
    start: date | None = None,
    end: date | None = None,
    *,
    fred: FredProvider | None = None,
    ecb: EcbProvider | None = None,
) -> MacroSeries:
    info = catalogue_entry(series_id)
    if info is None:
        raise UnknownSeriesError(f"unknown macro series {series_id!r}")
    if start is not None and end is not None and end < start:
        raise DataProviderError("end date is before start date")
    if info.source == "fred":
        if start is None:
            start = date(date.today().year - DEFAULT_LOOKBACK_YEARS, 1, 1)
        fetched = (fred or _fred).get_series(info.series_id, start, end)
    else:
        # ECB policy rates change a few times a year, so "five years back" can
        # hold no point at all: without a start the provider takes the newest
        # observations instead.
        fetched = (ecb or _ecb).get_series(info.ecb_flow or "", info.ecb_key or "", start)
        if end is not None:
            fetched = replace(fetched, points=[p for p in fetched.points if p[0] <= end])
            if not fetched.points:
                raise DataProviderError(f"no observations for {info.series_id} up to {end.isoformat()}")
    # A copy: the cached object is shared, and the label fields are ours to set.
    return replace(fetched, series_id=info.series_id, name=info.name, unit=info.unit, frequency=info.frequency)
