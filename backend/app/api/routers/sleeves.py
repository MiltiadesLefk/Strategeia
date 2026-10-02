from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.deps import get_app_settings, get_data_provider, get_session, require_auth
from app.config import AppSettings
from app.data_providers.base import DataProvider
from app.portfolio.models import Sleeve
from app.portfolio.sleeves import (
    CORE_SLEEVE_KEY,
    SleeveError,
    create_sleeve,
    delete_sleeve,
    get_sleeve,
    list_sleeves,
    scope_of,
    update_sleeve,
)
from app.portfolio.stats import compute_portfolio_stats
from app.schemas.portfolio_schemas import PortfolioStatsSchema
from app.schemas.sleeve_schemas import (
    SleeveCreateRequest,
    SleeveSchema,
    SleeveUpdateRequest,
    SleeveWithStatsSchema,
)
from app.services.sleeve_service import build_sleeve_engine

router = APIRouter(prefix="/api/sleeves", tags=["sleeves"], dependencies=[Depends(require_auth)])


def sleeve_to_schema(sleeve: Sleeve) -> SleeveSchema:
    return SleeveSchema(**sleeve.model_dump(), is_core=sleeve.key == CORE_SLEEVE_KEY)


def _http(exc: SleeveError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("", response_model=list[SleeveWithStatsSchema])
def list_all_sleeves(
    session: Session = Depends(get_session),
    data_provider: DataProvider = Depends(get_data_provider),
    settings: AppSettings = Depends(get_app_settings),
) -> list[SleeveWithStatsSchema]:
    """Every sleeve with its live statistics. The only write a GET here can make is
    seeding the built-in core sleeve row the first time it is looked at; no trade
    data and no equity point is written (the exit check runs with snapshot=False,
    like every other read of the portfolio)."""
    result = []
    for sleeve in list_sleeves(session):
        # Schema first: the engine's commits expire the ORM object (model_dump of an
        # expired instance comes back empty).
        base = sleeve_to_schema(sleeve)
        scope = scope_of(sleeve)
        build_sleeve_engine(session, data_provider, settings, sleeve).mark_to_market(snapshot=False)
        stats = compute_portfolio_stats(session, data_provider, settings.paper_starting_cash, scope)
        result.append(
            SleeveWithStatsSchema(**base.model_dump(), stats=PortfolioStatsSchema(**stats.__dict__))
        )
    return result


@router.post("", response_model=SleeveSchema, status_code=201)
def create(req: SleeveCreateRequest, session: Session = Depends(get_session)) -> SleeveSchema:
    try:
        return sleeve_to_schema(create_sleeve(session, req.name, req.style, req.starting_cash, req.notes))
    except SleeveError as exc:
        raise _http(exc) from exc


@router.patch("/{sleeve_ref}", response_model=SleeveSchema)
def update(sleeve_ref: str, req: SleeveUpdateRequest, session: Session = Depends(get_session)) -> SleeveSchema:
    """Rename, relabel, annotate or enable/disable a sleeve (by key or id)."""
    try:
        sleeve = get_sleeve(session, sleeve_ref)
        return sleeve_to_schema(
            update_sleeve(session, sleeve, name=req.name, style=req.style, enabled=req.enabled, notes=req.notes)
        )
    except SleeveError as exc:
        raise _http(exc) from exc


@router.delete("/{sleeve_ref}", status_code=204)
def delete(sleeve_ref: str, session: Session = Depends(get_session)) -> None:
    """Deletes a sleeve that never held a position or a plan; refuses otherwise."""
    try:
        delete_sleeve(session, get_sleeve(session, sleeve_ref))
    except SleeveError as exc:
        raise _http(exc) from exc
