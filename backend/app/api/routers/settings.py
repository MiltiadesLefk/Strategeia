from __future__ import annotations

from fastapi import APIRouter

from app.config import load_app_settings, update_app_settings
from app.data_providers.finnhub_provider import FinnhubProvider
from app.data_providers.base import DataProviderError
from app.llm_providers.factory import get_llm_provider
from app.schemas.settings_schemas import SettingsUpdateRequest, TestConnectionRequest, TestConnectionResponse

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings() -> dict:
    return load_app_settings().redacted()


@router.put("")
def put_settings(req: SettingsUpdateRequest) -> dict:
    changes = {k: v for k, v in req.model_dump().items() if v is not None}
    updated = update_app_settings(**changes)
    return updated.redacted()


@router.post("/test-connection", response_model=TestConnectionResponse)
def test_connection(req: TestConnectionRequest) -> TestConnectionResponse:
    settings = load_app_settings()

    if req.target == "llm":
        provider = get_llm_provider(settings)
        if not provider.is_configured():
            return TestConnectionResponse(ok=False, message=f"{provider.name} is not configured")
        result = provider.generate("Reply with exactly: OK", max_tokens=10)
        if result.error:
            return TestConnectionResponse(ok=False, message=result.error)
        return TestConnectionResponse(ok=True, message=f"{provider.name} responded in {result.latency_ms}ms")

    if req.target == "finnhub":
        if not settings.finnhub_api_key:
            return TestConnectionResponse(ok=False, message="No Finnhub API key configured")
        try:
            FinnhubProvider(settings.finnhub_api_key).get_quote("AAPL")
        except DataProviderError as exc:
            return TestConnectionResponse(ok=False, message=str(exc))
        return TestConnectionResponse(ok=True, message="Finnhub connection OK")

    return TestConnectionResponse(ok=False, message=f"Unknown test target: {req.target}")
