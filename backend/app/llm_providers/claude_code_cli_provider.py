from __future__ import annotations

import json
import shutil
import subprocess
import time

from app.llm_providers.base import LLMResult


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
    """

    name = "claude_code_cli"

    def __init__(self, cli_path: str | None = None, timeout_sec: int = 45):
        self.cli_path = cli_path or shutil.which("claude")
        self.timeout_sec = timeout_sec

    def is_configured(self) -> bool:
        return self.cli_path is not None

    def generate(self, prompt: str, *, max_tokens: int = 300, temperature: float = 0.4) -> LLMResult:
        if not self.cli_path:
            return LLMResult("", self.name, 0, error="Claude Code CLI not found on PATH")

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

        try:
            parsed = json.loads(proc.stdout)
            text = parsed.get("result", "") or ""
        except json.JSONDecodeError:
            text = proc.stdout.strip()

        if not text:
            return LLMResult("", self.name, latency_ms, error="Claude Code CLI returned empty output")
        return LLMResult(text=text, provider=self.name, latency_ms=latency_ms)
