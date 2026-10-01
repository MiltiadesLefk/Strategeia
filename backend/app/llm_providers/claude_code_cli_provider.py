from __future__ import annotations

import json
import shutil
import subprocess
import time

from app.config import normalize_claude_cli_model
from app.llm_providers.base import ROUTINE_TIER, LLMResult, LLMTier, model_for_tier


def _served_model(parsed: object) -> str | None:
    """The model that actually answered, from the CLI's JSON `modelUsage`.

    That map can list more than one model (the CLI makes small helper calls
    with a cheaper model alongside the real one), so the answering model is the
    one that cost the most. None when the field is missing or malformed: the
    caller then falls back to the model it asked for."""
    if not isinstance(parsed, dict):
        return None
    usage = parsed.get("modelUsage")
    if not isinstance(usage, dict) or not usage:
        return None
    best_name, best_cost = None, -1.0
    for name, stats in usage.items():
        cost = stats.get("costUSD") if isinstance(stats, dict) else None
        cost = cost if isinstance(cost, (int, float)) else 0.0
        if isinstance(name, str) and cost > best_cost:
            best_name, best_cost = name, cost
    return best_name


class ClaudeCodeCLIProvider:
    """Best-effort/novelty provider: shells out to the user's local Claude
    Code CLI (`claude -p`) instead of calling a metered API, so narrative
    generation rides on the user's existing Claude Code access.

    Trade-offs vs a direct API: no SLA, no documented rate limit for
    programmatic use, materially higher per-call latency (CLI cold start),
    consumes Claude Code usage rather than separate billing, and requires the
    CLI installed + authenticated on the machine running this backend. Not
    the recommended default for a long-running service (OpenRouterProvider
    is) but offered as a zero-incremental-cost option.

    `--allowedTools ""` denies all tool access so this is a pure
    text-completion call — no permission-mode override is used or needed,
    since a call with zero allowed tools can never trigger a permission
    prompt in the first place.

    `model` pins the model with `--model`. Without it the CLI uses whichever
    model was last chosen in an interactive session (or the account default),
    which would change the narratives and the AI overlay's objections without
    any change on our side. Blank means "don't pass --model".

    `decision_model` is the model for the decision tier (the AI overlay's
    verdict, see base.LLMTier); blank means the decision tier uses `model`.
    """

    name = "claude_code_cli"

    def __init__(
        self,
        cli_path: str | None = None,
        timeout_sec: int = 45,
        model: str | None = None,
        decision_model: str | None = None,
    ):
        self.cli_path = cli_path or shutil.which("claude")
        self.timeout_sec = timeout_sec
        # A bad value (e.g. a hand-edited setting) must never reach argv. The
        # provider is still built so one bad field can't take narrative
        # generation down with an exception; generate() reports the problem and
        # generate_with_fallback falls back to the rule-based text. A bad
        # decision model only breaks decision-tier calls, a bad routine model
        # only breaks the calls that would use it.
        self._model_error: str | None = None
        self._decision_model_error: str | None = None
        try:
            self.model = normalize_claude_cli_model(model or "")
        except ValueError as exc:
            self.model = ""
            self._model_error = str(exc)
        try:
            self.decision_model = normalize_claude_cli_model(decision_model or "")
        except ValueError as exc:
            self.decision_model = ""
            self._decision_model_error = str(exc)

    def model_for(self, tier: LLMTier = ROUTINE_TIER) -> str:
        return model_for_tier(self.model, self.decision_model, tier)

    def is_configured(self) -> bool:
        return self.cli_path is not None

    def generate(
        self, prompt: str, *, max_tokens: int = 300, temperature: float = 0.4, tier: LLMTier = ROUTINE_TIER
    ) -> LLMResult:
        if not self.cli_path:
            return LLMResult("", self.name, 0, error="Claude Code CLI not found on PATH")
        # Only the setting that tier would actually use can break the call.
        if tier == "decision" and self._decision_model_error:
            return LLMResult(
                "", self.name, 0, error=f"Invalid Claude CLI decision model setting: {self._decision_model_error}"
            )
        if self._model_error and not (tier == "decision" and self.decision_model):
            return LLMResult("", self.name, 0, error=f"Invalid Claude CLI model setting: {self._model_error}")
        model = self.model_for(tier)

        argv = [
            self.cli_path,
            "-p",
            "--output-format",
            "json",
            "--allowedTools",
            "",
            "--max-turns",
            "1",
        ]
        if model:
            argv += ["--model", model]
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_sec,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return LLMResult("", self.name, int(self.timeout_sec * 1000), error="Claude Code CLI timed out")
        except OSError as exc:
            return LLMResult("", self.name, 0, error=f"Claude Code CLI failed to start: {exc}")

        latency_ms = int((time.monotonic() - start) * 1000)
        if proc.returncode != 0:
            return LLMResult("", self.name, latency_ms, error=(proc.stderr or "non-zero exit")[:300])

        served: str | None = None
        try:
            parsed = json.loads(proc.stdout)
            text = (parsed.get("result", "") or "") if isinstance(parsed, dict) else ""
            served = _served_model(parsed)
        except json.JSONDecodeError:
            text = proc.stdout.strip()

        if not text:
            return LLMResult("", self.name, latency_ms, error="Claude Code CLI returned empty output")
        return LLMResult(text=text, provider=self.name, latency_ms=latency_ms, model=served or model or None)
