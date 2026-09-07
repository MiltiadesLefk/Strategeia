from __future__ import annotations

from app.config import AppSettings
from app.llm_providers.base import LLMProvider, LLMResult
from app.llm_providers.claude_code_cli_provider import ClaudeCodeCLIProvider
from app.llm_providers.gemini_provider import GeminiProvider
from app.llm_providers.null_provider import NullLLMProvider
from app.llm_providers.openai_provider import OpenAIProvider
from app.llm_providers.openrouter_provider import OpenRouterProvider


def get_llm_provider(settings: AppSettings) -> LLMProvider:
    if settings.llm_provider == "claude_code_cli":
        return ClaudeCodeCLIProvider()
    if settings.llm_provider == "openrouter":
        return OpenRouterProvider(settings.openrouter_api_key, settings.openrouter_model)
    if settings.llm_provider == "openai":
        return OpenAIProvider(settings.openai_api_key, settings.openai_model)
    if settings.llm_provider == "gemini":
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model)
    return NullLLMProvider()


def generate_with_fallback(provider: LLMProvider, prompt: str, fallback_text: str) -> LLMResult:
    """Every narrative call goes through this: a configured provider that
    errors (or the 'none' provider) never breaks the response, it just falls
    back to the deterministic rule-based text with the error attached."""
    if provider.name == "none" or not provider.is_configured():
        return LLMResult(text=fallback_text, provider="none", latency_ms=0)

    result = provider.generate(prompt)
    if result.error or not result.text:
        return LLMResult(text=fallback_text, provider="none", latency_ms=result.latency_ms, error=result.error)
    return result
