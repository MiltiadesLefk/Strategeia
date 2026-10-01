from __future__ import annotations

import time

import httpx

from app.llm_providers.base import ROUTINE_TIER, LLMPurpose, LLMResult, LLMTier, model_for_tier, web_search_wanted
from app.llm_providers.openai_compat import post_chat_completion

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
# Web search is a tool of the Responses API ({"type": "web_search"}); chat
# completions only offers it through special *-search-preview models that
# refuse temperature, so a research call with the web on uses this endpoint
# with the user's own model instead.
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_WEB_TOOL = {"type": "web_search"}


def parse_responses_output(data: object) -> tuple[str, list[str]]:
    """(text, cited URLs) from a Responses API reply: the `output_text`
    parts of its `message` items and their `url_citation` annotations."""
    texts: list[str] = []
    urls: list[str] = []
    output = data.get("output") if isinstance(data, dict) else None
    for item in output or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
            for ann in part.get("annotations") or []:
                url = ann.get("url") if isinstance(ann, dict) and ann.get("type") == "url_citation" else None
                if isinstance(url, str) and url and url not in urls:
                    urls.append(url)
    return "".join(texts), urls


class OpenAIProvider:
    name = "openai"
    supports_web_search = True
    web_search_note = "Uses OpenAI's web_search tool (Responses API); OpenAI bills each search call."

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", decision_model: str = ""):
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
            return LLMResult("", self.name, 0, error="OpenAI API key not configured")
        model = self.model_for(tier)
        start = time.monotonic()
        if web_search_wanted(purpose, web_search):
            return self._generate_with_web(prompt, model, max_tokens, start)
        try:
            resp = post_chat_completion(
                self.name,
                OPENAI_URL,
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
        # The API reports the model that actually served the call (an alias
        # resolves to a dated snapshot); fall back to the id asked for.
        served = data.get("model") if isinstance(data, dict) else None
        return LLMResult(
            text=text,
            provider=self.name,
            latency_ms=int((time.monotonic() - start) * 1000),
            model=served if isinstance(served, str) and served else model,
        )

    def _generate_with_web(self, prompt: str, model: str, max_tokens: int, start: float) -> LLMResult:
        # No structured output here: a research answer is prose with citations.
        # temperature is left out because the search-capable models reject it.
        try:
            resp = httpx.post(
                OPENAI_RESPONSES_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": model,
                    "input": prompt,
                    "tools": [dict(OPENAI_WEB_TOOL)],
                    "max_output_tokens": max(max_tokens, 16),
                },
                timeout=90,
            )
            resp.raise_for_status()
            data = resp.json()
            text, sources = parse_responses_output(data)
            if not text:
                raise ValueError("OpenAI returned no text")
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            return LLMResult("", self.name, int((time.monotonic() - start) * 1000), error=str(exc)[:300])
        served = data.get("model") if isinstance(data, dict) else None
        return LLMResult(
            text=text,
            provider=self.name,
            latency_ms=int((time.monotonic() - start) * 1000),
            model=served if isinstance(served, str) and served else model,
            web_search_used=True,
            sources=sources,
        )
