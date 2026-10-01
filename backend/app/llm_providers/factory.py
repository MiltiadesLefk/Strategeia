from __future__ import annotations

from app.config import AppSettings
from app.llm_providers.base import ROUTINE_TIER, LLMProvider, LLMResult, LLMTier
from app.llm_providers.claude_code_cli_provider import ClaudeCodeCLIProvider
from app.llm_providers.gemini_provider import GeminiProvider
from app.llm_providers.null_provider import NullLLMProvider
from app.llm_providers.openai_provider import OpenAIProvider
from app.llm_providers.openrouter_provider import OpenRouterProvider
from app.llm_providers.orcarouter_provider import OrcaRouterProvider


def get_llm_provider(settings: AppSettings) -> LLMProvider:
    if settings.llm_provider == "claude_code_cli":
        return ClaudeCodeCLIProvider(
            model=settings.claude_cli_model, decision_model=settings.claude_cli_decision_model
        )
    if settings.llm_provider == "openrouter":
        return OpenRouterProvider(
            settings.openrouter_api_key, settings.openrouter_model, settings.openrouter_decision_model
        )
    if settings.llm_provider == "orcarouter":
        return OrcaRouterProvider(
            settings.orcarouter_api_key, settings.orcarouter_model, settings.orcarouter_decision_model
        )
    if settings.llm_provider == "openai":
        return OpenAIProvider(settings.openai_api_key, settings.openai_model, settings.openai_decision_model)
    if settings.llm_provider == "gemini":
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model, settings.gemini_decision_model)
    return NullLLMProvider()


def generate_with_tier(provider: LLMProvider, prompt: str, tier: LLMTier = ROUTINE_TIER, **kwargs) -> LLMResult:
    """provider.generate(...) for a tier. The routine tier is the default, so
    it is not passed at all: a provider (or a test double) written before tiers
    existed keeps working for every narrative call. Only the decision tier is
    passed explicitly, and a provider that cannot take it is a bug worth a
    loud TypeError, not a silent downgrade of a decision to a routine model."""
    if tier == ROUTINE_TIER:
        return provider.generate(prompt, **kwargs)
    return provider.generate(prompt, tier=tier, **kwargs)


def generate_with_fallback(
    provider: LLMProvider, prompt: str, fallback_text: str, tier: LLMTier = ROUTINE_TIER
) -> LLMResult:
    """Every narrative call goes through this: a configured provider that
    errors (or the 'none' provider) never breaks the response, it just falls
    back to the deterministic rule-based text with the error attached.
    `tier` picks which model answers (see base.LLMTier); narratives are
    routine, which is the default."""
    if provider.name == "none" or not provider.is_configured():
        return LLMResult(text=fallback_text, provider="none", latency_ms=0)

    result = generate_with_tier(provider, prompt, tier)
    if result.error or not result.text:
        return LLMResult(text=fallback_text, provider="none", latency_ms=result.latency_ms, error=result.error)
    return result
