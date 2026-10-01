from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

# Which model answers a call. A tier, never a model name: callers say what the
# text is for and each provider maps that to a model from the settings.
#   routine  - narration (chart insight, research summary, the trade-plan take,
#              connection tests): speed and cost matter, nothing hangs on it.
#   decision - a call whose answer can stop a trade (the AI Trading Overlay's
#              verdict): worth a stronger model.
# A provider whose decision model is blank answers both tiers with its
# routine model, which is how an install that never sets one behaves.
LLMTier = Literal["routine", "decision"]
ROUTINE_TIER: LLMTier = "routine"
DECISION_TIER: LLMTier = "decision"


def model_for_tier(routine_model: str, decision_model: str, tier: LLMTier) -> str:
    """The model a provider should use for `tier`: its decision model for the
    decision tier when one is set, otherwise its routine model."""
    if tier == DECISION_TIER and decision_model:
        return decision_model
    return routine_model


@dataclass
class LLMResult:
    text: str
    provider: str
    latency_ms: int
    error: str | None = None
    # The model that answered. Every real provider fills it: the one the API (or
    # the CLI's usage report) says served the call, else the one asked for.
    # None only for the 'none' provider and for a Claude CLI call that pinned
    # no model and reported none. `provider` stays the plain provider name
    # because it is stored on trade plans as a string.
    model: str | None = None


class LLMProvider(Protocol):
    name: str

    # `response_schema` (a JSON schema dict) is optional and a hint: a provider
    # with a structured-output mode uses it, one without may omit the parameter
    # entirely (see structured.accepts_response_schema). The caller validates
    # the reply either way.
    def generate(
        self, prompt: str, *, max_tokens: int = 300, temperature: float = 0.4, tier: LLMTier = ROUTINE_TIER
    ) -> LLMResult: ...

    def is_configured(self) -> bool: ...
