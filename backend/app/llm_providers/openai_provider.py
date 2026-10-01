from __future__ import annotations

import time

import httpx

from app.llm_providers.base import ROUTINE_TIER, LLMResult, LLMTier, model_for_tier

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", decision_model: str = ""):
        self._api_key = api_key
        self._model = model
        self._decision_model = decision_model

    def model_for(self, tier: LLMTier = ROUTINE_TIER) -> str:
        return model_for_tier(self._model, self._decision_model, tier)

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def generate(
        self, prompt: str, *, max_tokens: int = 300, temperature: float = 0.4, tier: LLMTier = ROUTINE_TIER
    ) -> LLMResult:
        if not self._api_key:
            return LLMResult("", self.name, 0, error="OpenAI API key not configured")
        model = self.model_for(tier)
        start = time.monotonic()
        try:
            resp = httpx.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            return LLMResult("", self.name, int((time.monotonic() - start) * 1000), error=str(exc)[:300])
        # The API reports the model that actually served the call (an alias
        # resolves to a dated snapshot); fall back to the id asked for.
        served = data.get("model") if isinstance(data, dict) else None
        return LLMResult(
            text=text,
            provider=self.name,
            latency_ms=int((time.monotonic() - start) * 1000),
            model=served if isinstance(served, str) and served else model,
        )
