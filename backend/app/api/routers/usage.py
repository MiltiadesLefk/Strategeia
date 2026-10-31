"""Claude usage for the sidebar: the subscription's window percentages and what this app's AI calls used since it started."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_auth
from app.llm_providers import usage_meter
from app.services.claude_usage_service import get_session_usage

router = APIRouter(prefix="/api/usage", tags=["usage"], dependencies=[Depends(require_auth)])


@router.get("/session")
def session_usage() -> dict:
    window = get_session_usage()
    return {"available": window is not None, **(window or {}), "since_start": usage_meter.snapshot()}
