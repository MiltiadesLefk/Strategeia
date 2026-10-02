from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_data_provider, require_auth
from app.data_providers.base import DataProvider
from app.data_providers.universe_store import SYMBOL_PATTERN
from app.schemas.options_schemas import OptionsChainResponse
from app.services import options_chain_service as svc

router = APIRouter(prefix="/api/options", tags=["options"], dependencies=[Depends(require_auth)])

EXPIRATION_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@router.get("/{symbol}", response_model=OptionsChainResponse)
def options_chain(
    symbol: str,
    expiration: str | None = Query(None, description="YYYY-MM-DD, one of the listed expirations; the nearest when omitted"),
    strikes: int = Query(svc.DEFAULT_STRIKES_EACH_SIDE, ge=1, le=svc.MAX_STRIKES_EACH_SIDE, description="Strikes shown either side of the price"),
    data_provider: DataProvider = Depends(get_data_provider),
) -> OptionsChainResponse:
    """Read-only: one expiration's calls and puts plus a summary worked out from them."""
    symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.match(symbol):
        raise HTTPException(status_code=422, detail="Not a valid ticker symbol")
    if expiration is not None and not EXPIRATION_PATTERN.match(expiration):
        raise HTTPException(status_code=422, detail="expiration must look like 2026-12-18")
    try:
        return svc.get_options_view(data_provider, symbol, expiration, strikes)
    except svc.UnknownExpirationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
