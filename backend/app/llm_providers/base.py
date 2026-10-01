from __future__ import annotations

import re
from dataclasses import dataclass, field
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


# What a call is FOR, which decides whether web tools may ever be switched on.
#   narrate  - writing prose from numbers the rules already computed (default).
#   decide   - the AI overlay's verdict, the one call that can stop a trade.
#   research - gathering background for a research page (earnings previews,
#              theses, catalysts...). The ONLY purpose that may use web search,
#              and only when the research_mode setting allows it: a web page can
#              carry text that tries to steer a model, which is harmless in a
#              research note a person reads and unacceptable in a call whose
#              answer can stop a trade. See research_mode.research_tools_allowed.
LLMPurpose = Literal["narrate", "decide", "research"]
NARRATE_PURPOSE: LLMPurpose = "narrate"
DECIDE_PURPOSE: LLMPurpose = "decide"
RESEARCH_PURPOSE: LLMPurpose = "research"


def web_search_wanted(purpose: LLMPurpose, web_search: bool) -> bool:
    """Whether a provider should switch its web tool on for this call: only a
    research call that was explicitly cleared for it. Every provider asks this
    one function, so the rule is not re-implemented per provider."""
    return purpose == RESEARCH_PURPOSE and web_search


_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def extract_urls(text: str, limit: int = 20) -> list[str]:
    """Distinct http(s) URLs cited in `text`, in order, trailing punctuation trimmed."""
    seen: list[str] = []
    for match in _URL_RE.findall(text or ""):
        url = match.rstrip(".,;:!?")
        if url not in seen:
            seen.append(url)
        if len(seen) >= limit:
            break
    return seen


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
    # Web search accounting, filled only for a research call that asked for it.
    # `web_search_used` is True only when the provider really ran with its web
    # tool on; `sources` are the URLs the provider reported (or, for the Claude
    # CLI, that the answer cites); `note` says in plain words why a research
    # call that wanted the web did not get it (unsupported provider).
    web_search_used: bool = False
    sources: list[str] = field(default_factory=list)
    note: str | None = None


class LLMProvider(Protocol):
    name: str

    # `response_schema` (a JSON schema dict) is optional and a hint: a provider
    # with a structured-output mode uses it, one without may omit the parameter
    # entirely (see structured.accepts_response_schema). The caller validates
    # the reply either way.
    # `purpose` and `web_search` are only ever passed by a research call (see
    # research_mode.generate_for_research); everything else leaves them unset.
    #
    # A provider also exposes `supports_web_search: bool` and
    # `web_search_note: str` (what to tell the user when it has none).
    def generate(
        self, prompt: str, *, max_tokens: int = 300, temperature: float = 0.4, tier: LLMTier = ROUTINE_TIER
    ) -> LLMResult: ...

    def is_configured(self) -> bool: ...
