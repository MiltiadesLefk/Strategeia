from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_app_settings, require_auth
from app.config import AppSettings
from app.schemas.data_sources_schemas import DataSourcesResponse, ProbeResponse
from app.services import data_sources_service as service

router = APIRouter(prefix="/api/data-sources", tags=["data-sources"], dependencies=[Depends(require_auth)])

# A probe is a real request to a free public service; not a button to mash.
# Per source, in process (reset in tests/conftest.py).
_last_probe_monotonic: dict[str, float] = {}


@router.get("", response_model=DataSourcesResponse)
def list_data_sources(settings: AppSettings = Depends(get_app_settings)) -> DataSourcesResponse:
    """Every data source with live health. Read-only: it never calls a source."""
    return service.build_data_sources(settings)


@router.post("/{name}/probe", response_model=ProbeResponse)
def probe_data_source(name: str, settings: AppSettings = Depends(get_app_settings)) -> ProbeResponse:
    now = time.monotonic()
    last = _last_probe_monotonic.get(name)
    if last is not None and now - last < service.PROBE_COOLDOWN_SECONDS:
        wait = service.PROBE_COOLDOWN_SECONDS - (now - last)
        raise HTTPException(status_code=429, detail=f"{name} was just probed: wait {wait:.0f}s before probing it again.")
    try:
        result = service.run_probe(name, settings)
    except service.UnknownSourceError:
        raise HTTPException(status_code=404, detail=f"Unknown data source: {name}")
    except service.NotProbeableError:
        raise HTTPException(status_code=400, detail=f"{name} has no cheap request to probe with.")
    _last_probe_monotonic[name] = now
    return result
