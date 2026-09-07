from __future__ import annotations

import json
import subprocess

import pytest

from app.config import AppSettings
from app.llm_providers.base import LLMResult
from app.llm_providers.claude_code_cli_provider import ClaudeCodeCLIProvider
from app.llm_providers.factory import generate_with_fallback, get_llm_provider
from app.llm_providers.null_provider import NullLLMProvider
from app.llm_providers.openrouter_provider import OpenRouterProvider


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_null_provider_echoes_input():
    provider = NullLLMProvider()
    result = provider.generate("some fallback text")
    assert result.text == "some fallback text"
    assert result.provider == "none"
    assert provider.is_configured() is True


def test_claude_code_cli_not_configured_when_cli_is_not_on_path(monkeypatch):
    monkeypatch.setattr("app.llm_providers.claude_code_cli_provider.shutil.which", lambda name: None)
    provider = ClaudeCodeCLIProvider(cli_path=None)
    assert provider.is_configured() is False
    result = provider.generate("hello")
    assert result.error is not None


def test_claude_code_cli_parses_json_result(monkeypatch):
    monkeypatch.setattr(
        "app.llm_providers.claude_code_cli_provider.subprocess.run",
        lambda *a, **k: FakeCompletedProcess(returncode=0, stdout=json.dumps({"result": "hello there"})),
    )
    provider = ClaudeCodeCLIProvider(cli_path="/usr/bin/claude")
    result = provider.generate("prompt")
    assert result.text == "hello there"
    assert result.error is None


def test_claude_code_cli_falls_back_to_plain_stdout(monkeypatch):
    monkeypatch.setattr(
        "app.llm_providers.claude_code_cli_provider.subprocess.run",
        lambda *a, **k: FakeCompletedProcess(returncode=0, stdout="plain text answer"),
    )
    provider = ClaudeCodeCLIProvider(cli_path="/usr/bin/claude")
    result = provider.generate("prompt")
    assert result.text == "plain text answer"


def test_claude_code_cli_nonzero_exit_is_an_error(monkeypatch):
    monkeypatch.setattr(
        "app.llm_providers.claude_code_cli_provider.subprocess.run",
        lambda *a, **k: FakeCompletedProcess(returncode=1, stderr="boom"),
    )
    provider = ClaudeCodeCLIProvider(cli_path="/usr/bin/claude")
    result = provider.generate("prompt")
    assert result.error == "boom"


def test_claude_code_cli_timeout_is_an_error(monkeypatch):
    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=45)

    monkeypatch.setattr("app.llm_providers.claude_code_cli_provider.subprocess.run", raise_timeout)
    provider = ClaudeCodeCLIProvider(cli_path="/usr/bin/claude")
    result = provider.generate("prompt")
    assert "timed out" in result.error


def test_generate_with_fallback_uses_fallback_for_none_provider():
    result = generate_with_fallback(NullLLMProvider(), "prompt", "fallback text")
    assert result.text == "fallback text"
    assert result.provider == "none"


def test_generate_with_fallback_uses_fallback_when_unconfigured():
    provider = OpenRouterProvider(api_key="")
    result = generate_with_fallback(provider, "prompt", "fallback text")
    assert result.text == "fallback text"
    assert result.provider == "none"


def test_generate_with_fallback_uses_fallback_on_provider_error(monkeypatch):
    provider = OpenRouterProvider(api_key="fake-key")
    monkeypatch.setattr(provider, "generate", lambda prompt, **k: LLMResult("", "openrouter", 5, error="rate limited"))
    result = generate_with_fallback(provider, "prompt", "fallback text")
    assert result.text == "fallback text"
    assert result.error == "rate limited"


def test_get_llm_provider_selects_by_settings():
    settings = AppSettings(llm_provider="claude_code_cli")
    assert isinstance(get_llm_provider(settings), ClaudeCodeCLIProvider)

    settings = AppSettings(llm_provider="openrouter", openrouter_api_key="key123")
    provider = get_llm_provider(settings)
    assert isinstance(provider, OpenRouterProvider)

    settings = AppSettings(llm_provider="none")
    assert isinstance(get_llm_provider(settings), NullLLMProvider)
