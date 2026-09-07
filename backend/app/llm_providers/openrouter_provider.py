from __future__ import annotations

import time

import httpx

from app.llm_providers.base import LLMResult

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider:
    """Recommended default paid option: OpenAI-compatible gateway to many
    models via one key. Fits the user's existing OpenRouter credit."""

    name = "openrouter"

    def __init__(self, api_key: str, model: str = "anthropic/claude-3.5-haiku"):
        self._api_key = api_key
        self._model = model

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def generate(self, prompt: str, *, max_tokens: int = 300, temperature: float = 0.4) -> LLMResult:
        if not self._api_key:
            return LLMResult("", self.name, 0, error="OpenRouter API key not configured")
        start = time.monotonic()
        try:
            resp = httpx.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
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
        return LLMResult(text=text, provider=self.name, latency_ms=int((time.monotonic() - start) * 1000))
