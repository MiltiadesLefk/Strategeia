from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_auth
from app.data_providers import macro
from app.data_providers.base import DataProviderError
from app.schemas.macro_series_schemas import MacroPoint, MacroSeriesInfo, MacroSeriesResponse

router = APIRouter(prefix="/api/macro", tags=["macro"], dependencies=[Depends(require_auth)])


@router.get("/series", response_model=list[MacroSeriesInfo])
def list_macro_series() -> list[MacroSeriesInfo]:
    """The series on offer (no network call)."""
    return [
        MacroSeriesInfo(
            series_id=i.series_id, name=i.name, unit=i.unit, source=i.source, frequency=i.frequency, description=i.description
        )
        for i in macro.list_series()
    ]


@router.get("/series/{series_id}", response_model=MacroSeriesResponse)
def get_macro_series(
    series_id: str,
    start: date | None = Query(default=None, description="First date to include (YYYY-MM-DD)."),
    end: date | None = Query(default=None, description="Last date to include (YYYY-MM-DD)."),
) -> MacroSeriesResponse:
    """One macro series. Read-only: it fetches and caches, never stores anything."""
    try:
        series = macro.get_macro_series(series_id, start, end)
    except macro.UnknownSeriesError:
        raise HTTPException(status_code=404, detail=f"Unknown macro series: {series_id}")
    except DataProviderError as exc:
        # The source is down or has nothing for that range: a clean 502 with the reason.
        raise HTTPException(status_code=502, detail=f"No data available: {exc}")
    last = series.last_observation
    return MacroSeriesResponse(
        series_id=series.series_id,
        name=series.name,
        unit=series.unit,
        source=series.source,
        frequency=series.frequency,
        points=[MacroPoint(date=d, value=v) for d, v in series.points],
        last_updated=series.last_updated,
        last_observation_date=last[0] if last else None,
    )
