from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_data_provider, require_auth
from app.data_providers.base import DataProvider
from app.data_providers.universe_store import SYMBOL_PATTERN
from app.schemas.screener_schemas import (
    SavedScreen,
    SavedScreenCreate,
    ScreenerFieldsResponse,
    ScreenerRunRequest,
    ScreenerRunResponse,
)
from app.services import screener_service as svc
from app.services import screener_store
from app.services.screener_engine import FilterError

router = APIRouter(prefix="/api/screener", tags=["screener"], dependencies=[Depends(require_auth)])


@router.get("/fields", response_model=ScreenerFieldsResponse)
def fields() -> ScreenerFieldsResponse:
    """The field catalogue: names, units, which operators apply, which fields cost an extra fetch."""
    return svc.describe_fields()


@router.post("/run", response_model=ScreenerRunResponse)
def run(body: ScreenerRunRequest, data_provider: DataProvider = Depends(get_data_provider)) -> ScreenerRunResponse:
    """Read-only (POST only because the rules are a body): filter and sort the symbol list's cached data."""
    if body.symbols is not None:
        bad = [s for s in body.symbols if not SYMBOL_PATTERN.match(s.strip().upper())]
        if bad:
            raise HTTPException(status_code=422, detail=f"Not valid ticker symbols: {', '.join(bad[:5])}")
    try:
        return svc.run_screen(data_provider, body)
    except FilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/saved", response_model=list[SavedScreen])
def list_saved() -> list[SavedScreen]:
    return screener_store.list_saved()


@router.post("/saved", response_model=SavedScreen, status_code=201)
def save_screen(body: SavedScreenCreate) -> SavedScreen:
    try:
        svc.validate_spec(body)  # a screen that could never run is not worth keeping
        return screener_store.create_saved(body)
    except FilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except screener_store.DuplicateNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except screener_store.TooManyScreensError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/saved/{screen_id}")
def delete_screen(screen_id: str) -> dict[str, str]:
    if not screener_store.delete_saved(screen_id):
        raise HTTPException(status_code=404, detail="No saved screen with that id")
    return {"deleted": screen_id}
