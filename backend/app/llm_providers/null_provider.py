from __future__ import annotations

from app.llm_providers.base import ROUTINE_TIER, LLMResult, LLMTier


class NullLLMProvider:
    """Always available. Returns text the caller already built from rule-based
    templates (see app.analysis.insight_text) rather than calling any model —
    guarantees the app works with zero configuration."""

    name = "none"

    def is_configured(self) -> bool:
        return True

    def generate(
        self, prompt: str, *, max_tokens: int = 300, temperature: float = 0.4, tier: LLMTier = ROUTINE_TIER
    ) -> LLMResult:
        return LLMResult(text=prompt, provider=self.name, latency_ms=0)
