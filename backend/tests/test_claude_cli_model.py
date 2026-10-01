"""The Claude Code CLI provider is pinned to a model with `--model`.

Everything here uses a fake `subprocess.run`: no real CLI is ever started, so
no subscription usage is spent and nothing touches the network.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import AppSettings, normalize_claude_cli_model
from app.llm_providers.claude_code_cli_provider import ClaudeCodeCLIProvider
from app.llm_providers.factory import generate_with_fallback, get_llm_provider
from app.main import app
from app.schemas.settings_schemas import SettingsUpdateRequest

client = TestClient(app)

RUN = "app.llm_providers.claude_code_cli_provider.subprocess.run"


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RunRecorder:
    """Stands in for subprocess.run and remembers how it was called."""

    def __init__(self, stdout=None):
        self.stdout = stdout if stdout is not None else json.dumps({"result": "OK"})
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        return FakeCompletedProcess(returncode=0, stdout=self.stdout)

    @property
    def argv(self) -> list[str]:
        return self.calls[-1][0]

    @property
    def kwargs(self) -> dict:
        return self.calls[-1][1]


# --- argv -------------------------------------------------------------------


def test_model_flag_is_passed_exactly_once_when_set(monkeypatch):
    run = RunRecorder()
    monkeypatch.setattr(RUN, run)
    ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model="sonnet").generate("hi")
    assert run.argv.count("--model") == 1
    assert run.argv[run.argv.index("--model") + 1] == "sonnet"


def test_model_flag_is_absent_when_blank(monkeypatch):
    for blank in (None, "", "   "):
        run = RunRecorder()
        monkeypatch.setattr(RUN, run)
        ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model=blank).generate("hi")
        assert "--model" not in run.argv


def test_model_is_trimmed_before_it_reaches_argv(monkeypatch):
    run = RunRecorder()
    monkeypatch.setattr(RUN, run)
    ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model="  opus ").generate("hi")
    assert run.argv[run.argv.index("--model") + 1] == "opus"


@pytest.mark.parametrize("model", ["", "sonnet"])
def test_safety_flags_and_stdin_prompt_survive_pinning(monkeypatch, model):
    """The pin must not loosen anything: tools stay denied, one turn only, no
    permission-mode override, the prompt travels on stdin (never argv), and
    the command is never run through a shell."""
    run = RunRecorder()
    monkeypatch.setattr(RUN, run)
    ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model=model).generate("the secret prompt")
    argv = run.argv
    assert argv[:2] == ["/usr/bin/claude", "-p"]
    assert argv[argv.index("--allowedTools") + 1] == ""
    assert argv[argv.index("--max-turns") + 1] == "1"
    assert "--permission-mode" not in argv
    assert "bypassPermissions" not in argv
    assert "the secret prompt" not in argv
    assert run.kwargs["input"] == "the secret prompt"
    assert run.kwargs["shell"] is False


def test_invalid_model_never_reaches_argv_and_reports_an_error(monkeypatch):
    run = RunRecorder()
    monkeypatch.setattr(RUN, run)
    provider = ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model="--dangerously-skip-permissions")
    result = provider.generate("hi")
    assert run.calls == []
    assert result.error and "model" in result.error.lower()
    # ...and the fallback wrapper turns that into the rule-based text, not a 500.
    fallback = generate_with_fallback(provider, "hi", "rule-based text")
    assert fallback.text == "rule-based text"
    assert fallback.error == result.error


# --- which model answered ---------------------------------------------------


def test_result_reports_the_model_that_actually_answered(monkeypatch):
    """The CLI lists every model it used; a cheap helper model can appear
    next to the real one, so the answering model is the one that cost most."""
    stdout = json.dumps(
        {
            "result": "OK",
            "modelUsage": {
                "claude-haiku-4-5-20251001": {"costUSD": 0.0009},
                "claude-sonnet-5-5": {"costUSD": 0.085},
            },
        }
    )
    monkeypatch.setattr(RUN, RunRecorder(stdout=stdout))
    result = ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model="sonnet").generate("hi")
    assert result.model == "claude-sonnet-5-5"
    assert result.provider == "claude_code_cli"  # the stored column value is unchanged


def test_result_falls_back_to_the_requested_model_without_usage_info(monkeypatch):
    monkeypatch.setattr(RUN, RunRecorder(stdout="plain text"))
    assert ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model="opus").generate("hi").model == "opus"
    assert ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model="").generate("hi").model is None


def test_non_object_json_output_does_not_crash(monkeypatch):
    monkeypatch.setattr(RUN, RunRecorder(stdout="[1, 2]"))
    result = ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model="sonnet").generate("hi")
    assert result.error == "Claude Code CLI returned empty output"


# --- settings validation ----------------------------------------------------


def test_default_pin_is_the_sonnet_alias():
    assert AppSettings().claude_cli_model == "sonnet"


@pytest.mark.parametrize(
    "value",
    ["sonnet", "opus", "haiku", "claude-sonnet-5-5", "claude-opus-4-1-20250805", "sonnet[1m]", "a.b_c:d@e-1"],
)
def test_valid_model_names_are_accepted(value):
    assert normalize_claude_cli_model(value) == value
    assert AppSettings(claude_cli_model=value).claude_cli_model == value
    assert SettingsUpdateRequest(claude_cli_model=value).claude_cli_model == value


def test_blank_means_unpinned_and_is_trimmed():
    assert AppSettings(claude_cli_model="").claude_cli_model == ""
    assert AppSettings(claude_cli_model="   ").claude_cli_model == ""
    assert SettingsUpdateRequest(claude_cli_model="  ").claude_cli_model == ""
    assert AppSettings(claude_cli_model="  sonnet ").claude_cli_model == "sonnet"


@pytest.mark.parametrize(
    "value",
    [
        "--model",
        "-x",
        "sonnet opus",
        "sonnet; rm -rf /",
        "$(whoami)",
        "so`nnet",
        "son\nnet",
        "sonnet&",
        "a/b",
        "x" * 65,
    ],
)
def test_bad_model_names_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_claude_cli_model(value)
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(claude_cli_model=value)
    with pytest.raises(ValidationError):
        AppSettings(claude_cli_model=value)


def test_put_settings_rejects_a_bad_model_with_422():
    resp = client.put("/api/settings", json={"claude_cli_model": "sonnet; whoami"})
    assert resp.status_code == 422


def test_put_settings_passes_a_trimmed_model_and_a_blank_through(monkeypatch):
    seen: list[dict] = []

    def fake_update(**changes):
        seen.append(changes)
        return AppSettings(**changes)

    monkeypatch.setattr("app.api.routers.settings.update_app_settings", fake_update)
    assert client.put("/api/settings", json={"claude_cli_model": " opus "}).json()["claude_cli_model"] == "opus"
    assert client.put("/api/settings", json={"claude_cli_model": ""}).json()["claude_cli_model"] == ""
    assert seen == [{"claude_cli_model": "opus"}, {"claude_cli_model": ""}]


def test_omitting_the_field_leaves_it_unchanged(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(
        "app.api.routers.settings.update_app_settings", lambda **c: seen.append(c) or AppSettings()
    )
    client.put("/api/settings", json={"min_confidence_for_trade": 40})
    assert "claude_cli_model" not in seen[0]


# --- factory wiring ---------------------------------------------------------


def test_factory_passes_the_setting_to_the_provider():
    provider = get_llm_provider(AppSettings(llm_provider="claude_code_cli", claude_cli_model="opus"))
    assert isinstance(provider, ClaudeCodeCLIProvider)
    assert provider.model == "opus"

    unpinned = get_llm_provider(AppSettings(llm_provider="claude_code_cli", claude_cli_model=""))
    assert unpinned.model == ""

    assert get_llm_provider(AppSettings(llm_provider="claude_code_cli")).model == "sonnet"


# --- status + test-connection ----------------------------------------------


def _use_settings(monkeypatch, **fields):
    monkeypatch.setattr(
        "app.api.routers.settings.load_app_settings", lambda: AppSettings(llm_provider="claude_code_cli", **fields)
    )
    monkeypatch.setattr("app.llm_providers.claude_code_cli_provider.shutil.which", lambda name: "/usr/bin/claude")


def test_status_shows_the_pinned_model(monkeypatch):
    _use_settings(monkeypatch, claude_cli_model="sonnet")
    body = client.get("/api/settings/status").json()
    assert body["ai_provider"] == "claude_code_cli"  # unchanged
    assert body["ai_model"] == "sonnet"


def test_status_model_is_empty_when_unpinned_or_not_a_cli_provider(monkeypatch):
    _use_settings(monkeypatch, claude_cli_model="")
    assert client.get("/api/settings/status").json()["ai_model"] == ""

    monkeypatch.setattr(
        "app.api.routers.settings.load_app_settings",
        lambda: AppSettings(llm_provider="openrouter", openrouter_api_key="k"),
    )
    assert client.get("/api/settings/status").json()["ai_model"] == ""


def test_test_connection_message_names_the_model(monkeypatch):
    _use_settings(monkeypatch, claude_cli_model="sonnet")
    stdout = json.dumps({"result": "OK", "modelUsage": {"claude-sonnet-5-5": {"costUSD": 0.08}}})
    run = RunRecorder(stdout=stdout)
    monkeypatch.setattr(RUN, run)
    body = client.post("/api/settings/test-connection", json={"target": "llm"}).json()
    assert body["ok"] is True
    assert "claude-sonnet-5-5" in body["message"]
    assert "sonnet" in body["message"]
    assert run.argv[run.argv.index("--model") + 1] == "sonnet"


def test_test_connection_message_says_when_unpinned(monkeypatch):
    _use_settings(monkeypatch, claude_cli_model="")
    monkeypatch.setattr(RUN, RunRecorder(stdout=json.dumps({"result": "OK"})))
    body = client.post("/api/settings/test-connection", json={"target": "llm"}).json()
    assert body["ok"] is True
    assert "not pinned" in body["message"]
