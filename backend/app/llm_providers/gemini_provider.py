from __future__ import annotations

import logging
import time

from urllib.parse import quote

import httpx

from app.config import normalize_api_model
from app.llm_providers.base import ROUTINE_TIER, LLMPurpose, LLMResult, LLMTier, model_for_tier, web_search_wanted
from app.llm_providers.structured import gemini_response_schema

logger = logging.getLogger(__name__)

GEMINI_WEB_TOOLS = [{"google_search": {}}]
GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiProvider:
    name = "gemini"
    supports_web_search = True
    web_search_note = (
        "Uses Gemini's Grounding with Google Search; Google bills grounded prompts separately "
        "beyond its free allowance."
    )

    def __init__(self, api_key: str, model: str = "gemini-1.5-flash", decision_model: str = ""):
        self._api_key = api_key
        self._model = model
        self._decision_model = decision_model

    def model_for(self, tier: LLMTier = ROUTINE_TIER) -> str:
        return model_for_tier(self._model, self._decision_model, tier)

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _post(self, url: str, prompt: str, generation_config: dict, tools: list | None = None) -> httpx.Response:
        body: dict = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": generation_config}
        if tools:
            body["tools"] = tools
        resp = httpx.post(url, params={"key": self._api_key}, json=body, timeout=90 if tools else 30)
        resp.raise_for_status()
        return resp

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 300,
        temperature: float = 0.4,
        tier: LLMTier = ROUTINE_TIER,
        response_schema: dict | None = None,
        purpose: LLMPurpose = "narrate",
        web_search: bool = False,
    ) -> LLMResult:
        if not self._api_key:
            return LLMResult("", self.name, 0, error="Gemini API key not configured")
        model = self.model_for(tier)
        # The model id is part of the URL path, so a hand-edited setting that
        # holds anything but a plain id (a "/" or "?" would redirect the request)
        # is refused here as well as when the setting is saved.
        try:
            if not normalize_api_model(model, allow_slash=False):
                raise ValueError("no model name is set")
        except ValueError as exc:
            return LLMResult("", self.name, 0, error=f"Invalid Gemini model setting: {exc}")
        generation_config: dict = {"maxOutputTokens": max_tokens, "temperature": temperature}
        use_web = web_search_wanted(purpose, web_search)
        # Gemini cannot combine Google Search grounding with a JSON response
        # schema, so a web call is prose with citations (the caller's lenient
        # parser reads it if it wanted JSON).
        if response_schema and not use_web:
            # Gemini's structured mode: JSON mime type plus a response schema
            # in the OpenAPI subset it accepts (see structured.gemini_response_schema).
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = gemini_response_schema(response_schema)
        url = GEMINI_URL_TEMPLATE.format(model=quote(model, safe=""))
        start = time.monotonic()
        try:
            try:
                resp = self._post(url, prompt, generation_config, GEMINI_WEB_TOOLS if use_web else None)
            except httpx.HTTPStatusError as exc:
                # A model that cannot take the response schema answers 400:
                # retry once without it (the caller's lenient parser then reads
                # the reply) rather than lose the whole answer.
                if not response_schema or exc.response is None or exc.response.status_code != 400:
                    raise
                logger.warning(
                    "Gemini rejected structured output for model %s (HTTP 400); retrying once without it, "
                    "so this reply will be read leniently.",
                    model,
                )
                plain_config = {
                    k: v for k, v in generation_config.items() if k not in ("responseMimeType", "responseSchema")
                }
                resp = self._post(url, prompt, plain_config)
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            return LLMResult("", self.name, int((time.monotonic() - start) * 1000), error=str(exc)[:300])
        # `modelVersion` is the model that actually served the call.
        served = data.get("modelVersion") if isinstance(data, dict) else None
        return LLMResult(
            text=text,
            provider=self.name,
            latency_ms=int((time.monotonic() - start) * 1000),
            model=served if isinstance(served, str) and served else model,
            web_search_used=use_web,
            sources=_grounding_urls(data) if use_web else [],
        )


def _grounding_urls(data: object) -> list[str]:
    """The pages Gemini grounded its answer on, from `groundingMetadata`.
    These are often Google redirect links; they are reported as given."""
    try:
        chunks = data["candidates"][0]["groundingMetadata"].get("groundingChunks") or []  # type: ignore[index]
    except (KeyError, IndexError, TypeError, AttributeError):
        return []
    urls: list[str] = []
    for chunk in chunks:
        web = chunk.get("web") if isinstance(chunk, dict) else None
        uri = web.get("uri") if isinstance(web, dict) else None
        if isinstance(uri, str) and uri and uri not in urls:
            urls.append(uri)
    return urls
