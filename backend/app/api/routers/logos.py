"""Stored company logos. Serves the file kept on disk and, when a logo is not there yet, downloads it once."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import require_auth
from app.services import logo_service

router = APIRouter(prefix="/api/logos", tags=["logos"], dependencies=[Depends(require_auth)])

# The files come from a third party: whatever opens one directly gets no scripts, no network, nothing.
HEADERS = {
    "Cache-Control": "public, max-age=86400",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
}


@router.get("/{symbol}")
def get_logo(symbol: str) -> FileResponse:
    clean = logo_service.clean_symbol(symbol)
    if clean is None:
        raise HTTPException(status_code=404, detail="No such logo")
    path = logo_service.cached_logo(clean)
    if path is None and logo_service.fetch_logo(clean) == "stored":
        path = logo_service.cached_logo(clean)
    if path is None:
        raise HTTPException(status_code=404, detail="No logo for this symbol")
    return FileResponse(path, media_type="image/svg+xml" if path.suffix == ".svg" else "image/png", headers=HEADERS)
