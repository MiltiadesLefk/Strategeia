from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analysis.screen_presets import get_preset
from app.api.deps import get_app_settings, get_data_provider, require_auth
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.data_providers.universe import get_default_watchlist
from app.schemas.screen_preset_schemas import PresetRunResponse, PresetSchema
from app.services.screen_preset_service import PRESET_DEFAULT_LIMIT, PRESET_MAX_LIMIT, list_presets, run_preset

router = APIRouter(prefix="/api/scan/presets", tags=["scanner"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[PresetSchema])
def presets() -> list[PresetSchema]:
    return list_presets()


@router.get("/{name}", response_model=PresetRunResponse)
def run(
    name: str,
    limit: int = Query(PRESET_DEFAULT_LIMIT, ge=1, le=PRESET_MAX_LIMIT, description="How many watchlist symbols to screen"),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> PresetRunResponse:
    """Run one preset over the first `limit` symbols of the effective watchlist. Read-only."""
    preset = get_preset(name)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"Unknown preset: {name}")
    return run_preset(preset, get_default_watchlist(settings.scan_universe_size), data_provider, limit=limit)
