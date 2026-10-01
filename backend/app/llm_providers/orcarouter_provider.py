from __future__ import annotations

import time

import httpx

from app.llm_providers.base import ROUTINE_TIER, LLMPurpose, LLMResult, LLMTier, model_for_tier, web_search_wanted
from app.llm_providers.openai_compat import post_chat_completion

ORCAROUTER_URL = "https://api.orcarouter.ai/v1/chat/completions"


class OrcaRouterProvider:
    """OpenAI-compatible gateway (Continuum AI Corp, api.orcarouter.ai) —
    zero-markup pass-through pricing across 200+ models via one key. Default
    model 'orcarouter/auto' lets the gateway's adaptive router pick a model
    per-request rather than pinning one."""

    name = "orcarouter"
    # OrcaRouter documents no web-search option (no plugin, no ":online"
    # model, no search tool) to turn on, so a research call asking for the web
    # runs on our data only and says so rather than send an option the gateway
    # may reject or silently ignore.
    supports_web_search = False
    web_search_note = "OrcaRouter has no documented web search, so this answer used our data only."

    def __init__(self, api_key: str, model: str = "orcarouter/auto", decision_model: str = ""):
        self._api_key = api_key
        self._model = model
        self._decision_model = decision_model

    def model_for(self, tier: LLMTier = ROUTINE_TIER) -> str:
        return model_for_tier(self._model, self._decision_model, tier)

    def is_configured(self) -> bool:
        return bool(self._api_key)

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
            return LLMResult("", self.name, 0, error="OrcaRouter API key not configured")
        model = self.model_for(tier)
        start = time.monotonic()
        try:
            resp = post_chat_completion(
                self.name,
                ORCAROUTER_URL,
                self._api_key,
                {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                response_schema=response_schema,
            )
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            return LLMResult("", self.name, int((time.monotonic() - start) * 1000), error=str(exc)[:300])
        # The API reports the model that actually served the call (a gateway
        # router like orcarouter/auto resolves to a concrete one); fall back to
        # the id asked for.
        served = data.get("model") if isinstance(data, dict) else None
        return LLMResult(
            text=text,
            provider=self.name,
            latency_ms=int((time.monotonic() - start) * 1000),
            model=served if isinstance(served, str) and served else model,
        )
