from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.api.deps import require_auth
from app.markets import us_market_session
from app.schemas.market_schemas import MarketSessionResponse

router = APIRouter(prefix="/api/market", tags=["market"], dependencies=[Depends(require_auth)])


def _now() -> datetime:
    """Separate so a test can pin the moment the endpoint describes."""
    return datetime.now(timezone.utc)


@router.get("/session", response_model=MarketSessionResponse)
def market_session() -> MarketSessionResponse:
    now = _now()
    return MarketSessionResponse(**asdict(us_market_session(now)), as_of=now)
