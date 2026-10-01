from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.deps import get_app_settings, get_session, require_auth
from app.backtest.replay import REPLAYABLE_KNOBS, UnreplayableKnobError, parse_overrides, replay_from_session
from app.config import AppSettings
from app.schemas.replay_schemas import ReplayRequest, ReplayResultSchema

router = APIRouter(prefix="/api/replay", tags=["replay"], dependencies=[Depends(require_auth)])


@router.post("", response_model=ReplayResultSchema)
def replay_settings_change(
    body: ReplayRequest,
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
) -> ReplayResultSchema:
    """What a settings change would have done to the decisions already made.

    Read-only: it reads the stored plans, positions and missed-trade outcomes,
    computes, and returns. Nothing is written or fetched (it is a POST only
    because the overrides travel in the body). A setting it cannot recompute
    from stored rows (sizing, exits, caps) is refused with the reason."""
    try:
        overrides = parse_overrides(body.overrides)
    except UnreplayableKnobError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "These settings cannot be replayed from stored decisions.", "rejected": exc.rejected,
                    "replayable": REPLAYABLE_KNOBS},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "rejected": {}, "replayable": REPLAYABLE_KNOBS}) from exc
    return ReplayResultSchema.model_validate(replay_from_session(session, settings, overrides))
