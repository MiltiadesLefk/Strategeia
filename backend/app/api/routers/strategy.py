from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import get_app_settings, get_session, require_auth
from app.config import AppSettings
from app.schemas.strategy_schemas import StrategyHistoryResponse, StrategyVersionSchema
from app.strategy.service import strategy_history

router = APIRouter(prefix="/api/strategy", tags=["strategy"], dependencies=[Depends(require_auth)])


@router.get("/versions", response_model=StrategyHistoryResponse)
def list_strategy_versions(
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
) -> StrategyHistoryResponse:
    """Read-only: versions are created when a plan is generated, never here."""
    history = strategy_history(session, settings)
    return StrategyHistoryResponse(
        versions=[
            StrategyVersionSchema(
                id=item.version.id,
                number=item.version.number,
                fingerprint=item.version.fingerprint,
                created_at=item.version.created_at,
                label=item.version.label,
                settings_snapshot=item.snapshot,
                changes=item.changes,
                plans=item.plans,
                no_trades=item.no_trades,
                positions_opened=item.positions_opened,
                closed_trades=item.closed_trades,
            )
            for item in history.versions
        ],
        unversioned_plans=history.unversioned_plans,
        current_number=history.current_number,
    )
