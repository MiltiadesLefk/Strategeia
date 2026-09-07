from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResult:
    text: str
    provider: str
    latency_ms: int
    error: str | None = None


class LLMProvider(Protocol):
    name: str

    def generate(self, prompt: str, *, max_tokens: int = 300, temperature: float = 0.4) -> LLMResult: ...

    def is_configured(self) -> bool: ...
