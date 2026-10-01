"""Two model tiers per LLM provider: "routine" (narration) and "decision" (the AI
Trading Overlay's verdict).

Every HTTP call is mocked and `subprocess.run` is faked, so no network is touched
and no Claude subscription usage is spent.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from app.config import AppSettings, normalize_api_model
from app.llm_providers.base import LLMResult, model_for_tier
from app.llm_providers.claude_code_cli_provider import ClaudeCodeCLIProvider
from app.llm_providers.factory import generate_with_fallback, generate_with_tier, get_llm_provider
from app.llm_providers.gemini_provider import GeminiProvider
from app.llm_providers.null_provider import NullLLMProvider
from app.llm_providers.openai_provider import OpenAIProvider
from app.llm_providers.openrouter_provider import OpenRouterProvider
from app.llm_providers.orcarouter_provider import OrcaRouterProvider
from app.main import app
from app.schemas import settings_schemas
from app.schemas.settings_schemas import SettingsUpdateRequest
from app.services import analysis_service, research_service, trade_plan_service
from app.strategy.snapshot import build_snapshot, describe_changes

client = TestClient(app)

RUN = "app.llm_providers.claude_code_cli_provider.subprocess.run"

DECISION_FIELDS = [
    "claude_cli_decision_model",
    "openrouter_decision_model",
    "orcarouter_decision_model",
    "openai_decision_model",
    "gemini_decision_model",
]


# --- fakes -------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class PostRecorder:
    """Stands in for httpx.post and remembers the URL and JSON body."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)

    @property
    def url(self) -> str:
        return self.calls[-1][0]

    @property
    def body(self) -> dict:
        return self.calls[-1][1]["json"]


class FakeCompletedProcess:
    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RunRecorder:
    def __init__(self, stdout: str | None = None):
        self.stdout = stdout if stdout is not None else json.dumps({"result": "OK"})
        self.argvs: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.argvs.append(list(argv))
        return FakeCompletedProcess(stdout=self.stdout)

    @property
    def argv(self) -> list[str]:
        return self.argvs[-1]


def _model_arg(argv: list[str]) -> str | None:
    return argv[argv.index("--model") + 1] if "--model" in argv else None


def _chat_payload(model: str | None = None) -> dict:
    payload: dict = {"choices": [{"message": {"content": "hello"}}]}
    if model is not None:
        payload["model"] = model
    return payload


class TierSpy:
    """A configured provider that records which tier each call asked for."""

    name = "spy"

    def __init__(self, text: str = "some text", model_by_tier: dict[str, str] | None = None):
        self.text = text
        self.model_by_tier = model_by_tier or {}
        self.tiers: list[str] = []

    def is_configured(self) -> bool:
        return True

    def generate(self, prompt, *, max_tokens=300, temperature=0.4, tier="routine") -> LLMResult:
        self.tiers.append(tier)
        return LLMResult(text=self.text, provider=self.name, latency_ms=1, model=self.model_by_tier.get(tier))


class LegacyProvider:
    """Written before tiers existed: generate() takes no `tier` argument."""

    name = "legacy"

    def is_configured(self) -> bool:
        return True

    def generate(self, prompt, *, max_tokens=300, temperature=0.4) -> LLMResult:
        return LLMResult(text="legacy answer", provider=self.name, latency_ms=1)


# --- tier -> model mapping ---------------------------------------------------


def test_model_for_tier_uses_the_decision_model_only_for_the_decision_tier():
    assert model_for_tier("cheap", "strong", "routine") == "cheap"
    assert model_for_tier("cheap", "strong", "decision") == "strong"
    assert model_for_tier("cheap", "", "decision") == "cheap"  # blank = same as routine
    assert model_for_tier("", "strong", "routine") == ""


@pytest.mark.parametrize(
    "provider_cls, url",
    [
        (OpenRouterProvider, "https://openrouter.ai/api/v1/chat/completions"),
        (OrcaRouterProvider, "https://api.orcarouter.ai/v1/chat/completions"),
        (OpenAIProvider, "https://api.openai.com/v1/chat/completions"),
    ],
)
def test_openai_style_providers_send_the_model_for_each_tier(monkeypatch, provider_cls, url):
    post = PostRecorder(_chat_payload())
    monkeypatch.setattr(httpx, "post", post)
    provider = provider_cls("key", "cheap-model", "strong-model")

    provider.generate("p")
    assert post.url == url
    assert post.body["model"] == "cheap-model"

    provider.generate("p", tier="routine")
    assert post.body["model"] == "cheap-model"

    provider.generate("p", tier="decision")
    assert post.body["model"] == "strong-model"


@pytest.mark.parametrize("provider_cls", [OpenRouterProvider, OrcaRouterProvider, OpenAIProvider])
def test_blank_decision_model_sends_the_routine_model_on_both_tiers(monkeypatch, provider_cls):
    post = PostRecorder(_chat_payload())
    monkeypatch.setattr(httpx, "post", post)
    for decision_model in ("", None):
        provider = provider_cls("key", "cheap-model", decision_model) if decision_model is not None else provider_cls("key", "cheap-model")
        provider.generate("p", tier="decision")
        assert post.body["model"] == "cheap-model"


def test_gemini_puts_the_tiers_model_in_the_url(monkeypatch):
    post = PostRecorder({"candidates": [{"content": {"parts": [{"text": "hello"}]}}]})
    monkeypatch.setattr(httpx, "post", post)
    provider = GeminiProvider("key", "gemini-flash", "gemini-pro")

    provider.generate("p")
    assert post.url.endswith("/models/gemini-flash:generateContent")
    provider.generate("p", tier="decision")
    assert post.url.endswith("/models/gemini-pro:generateContent")

    blank = GeminiProvider("key", "gemini-flash")
    blank.generate("p", tier="decision")
    assert post.url.endswith("/models/gemini-flash:generateContent")


@pytest.mark.parametrize("bad", ["a/b", "x?key=1", "../v1", "a b", "-x"])
def test_gemini_refuses_a_model_that_is_not_a_plain_id_without_calling_out(monkeypatch, bad):
    post = PostRecorder({})
    monkeypatch.setattr(httpx, "post", post)
    # A bad decision model only breaks decision calls; the routine one still works.
    result = GeminiProvider("key", "gemini-flash", bad).generate("p", tier="decision")
    assert result.error and "Invalid Gemini model" in result.error
    assert post.calls == []
    # ...and the same goes for a bad routine model.
    assert "Invalid Gemini model" in (GeminiProvider("key", bad).generate("p").error or "")
    assert post.calls == []


def test_gemini_refuses_a_blank_model(monkeypatch):
    post = PostRecorder({})
    monkeypatch.setattr(httpx, "post", post)
    result = GeminiProvider("key", "").generate("p", tier="decision")
    assert result.error and "Invalid Gemini model" in result.error
    assert post.calls == []


def test_claude_cli_argv_per_tier(monkeypatch):
    run = RunRecorder()
    monkeypatch.setattr(RUN, run)
    provider = ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model="haiku", decision_model="opus")

    provider.generate("p")
    assert _model_arg(run.argv) == "haiku"
    provider.generate("p", tier="routine")
    assert _model_arg(run.argv) == "haiku"
    provider.generate("p", tier="decision")
    assert _model_arg(run.argv) == "opus"
    assert run.argv.count("--model") == 1


def test_claude_cli_blank_decision_model_uses_the_routine_model(monkeypatch):
    run = RunRecorder()
    monkeypatch.setattr(RUN, run)
    ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model="sonnet").generate("p", tier="decision")
    assert _model_arg(run.argv) == "sonnet"
    ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model="sonnet", decision_model="  ").generate("p", tier="decision")
    assert _model_arg(run.argv) == "sonnet"


def test_claude_cli_decision_model_works_when_the_routine_one_is_unpinned(monkeypatch):
    run = RunRecorder()
    monkeypatch.setattr(RUN, run)
    provider = ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model="", decision_model="opus")
    provider.generate("p")
    assert _model_arg(run.argv) is None
    provider.generate("p", tier="decision")
    assert _model_arg(run.argv) == "opus"


def test_claude_cli_keeps_every_safety_flag_on_the_decision_tier(monkeypatch):
    run = RunRecorder()
    monkeypatch.setattr(RUN, run)
    ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model="sonnet", decision_model="opus").generate(
        "p", tier="decision"
    )
    argv = run.argv
    assert argv[argv.index("--allowedTools") + 1] == ""
    assert argv[argv.index("--max-turns") + 1] == "1"
    assert "--permission-mode" not in argv


def test_claude_cli_bad_decision_model_never_reaches_argv_and_only_breaks_decision_calls(monkeypatch):
    run = RunRecorder()
    monkeypatch.setattr(RUN, run)
    provider = ClaudeCodeCLIProvider(
        cli_path="/usr/bin/claude", model="sonnet", decision_model="--dangerously-skip-permissions"
    )
    bad = provider.generate("p", tier="decision")
    assert run.argvs == []
    assert bad.error and "decision model" in bad.error.lower()
    fallback = generate_with_fallback(provider, "p", "rule-based text", tier="decision")
    assert fallback.text == "rule-based text" and fallback.error == bad.error
    # the routine tier is unaffected
    assert provider.generate("p").error is None
    assert _model_arg(run.argv) == "sonnet"


def test_claude_cli_bad_routine_model_does_not_break_a_valid_decision_model(monkeypatch):
    run = RunRecorder()
    monkeypatch.setattr(RUN, run)
    provider = ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model="bad model", decision_model="opus")
    assert provider.generate("p").error  # routine: refused
    assert provider.generate("p", tier="decision").error is None
    assert _model_arg(run.argv) == "opus"


def test_factory_builds_each_provider_with_its_decision_model():
    settings = AppSettings(
        claude_cli_model="sonnet",
        claude_cli_decision_model="opus",
        openrouter_api_key="k",
        openrouter_model="r/cheap",
        openrouter_decision_model="r/strong",
        orcarouter_model="o/cheap",
        orcarouter_decision_model="o/strong",
        openai_model="gpt-cheap",
        openai_decision_model="gpt-strong",
        gemini_model="gem-cheap",
        gemini_decision_model="gem-strong",
    )
    expected = {
        "claude_code_cli": ("sonnet", "opus"),
        "openrouter": ("r/cheap", "r/strong"),
        "orcarouter": ("o/cheap", "o/strong"),
        "openai": ("gpt-cheap", "gpt-strong"),
        "gemini": ("gem-cheap", "gem-strong"),
    }
    for name, (routine, decision) in expected.items():
        provider = get_llm_provider(settings.model_copy(update={"llm_provider": name}))
        assert provider.model_for("routine") == routine
        assert provider.model_for("decision") == decision
        assert settings.model_copy(update={"llm_provider": name}).effective_decision_model() == decision


# --- LLMResult.model ----------------------------------------------------------


@pytest.mark.parametrize("provider_cls", [OpenRouterProvider, OrcaRouterProvider, OpenAIProvider])
def test_openai_style_result_reports_the_model_the_api_says_served_the_call(monkeypatch, provider_cls):
    monkeypatch.setattr(httpx, "post", PostRecorder(_chat_payload(model="served-snapshot-2026")))
    result = provider_cls("key", "alias").generate("p")
    assert result.model == "served-snapshot-2026"
    assert result.provider == provider_cls.name


@pytest.mark.parametrize("provider_cls", [OpenRouterProvider, OrcaRouterProvider, OpenAIProvider])
def test_openai_style_result_falls_back_to_the_requested_model(monkeypatch, provider_cls):
    monkeypatch.setattr(httpx, "post", PostRecorder(_chat_payload()))
    provider = provider_cls("key", "cheap", "strong")
    assert provider.generate("p").model == "cheap"
    assert provider.generate("p", tier="decision").model == "strong"


def test_gemini_result_reports_model_version_else_the_requested_model(monkeypatch):
    body = {"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}
    monkeypatch.setattr(httpx, "post", PostRecorder({**body, "modelVersion": "gemini-pro-002"}))
    assert GeminiProvider("key", "gemini-pro").generate("p").model == "gemini-pro-002"
    monkeypatch.setattr(httpx, "post", PostRecorder(body))
    assert GeminiProvider("key", "gemini-pro").generate("p").model == "gemini-pro"


def test_claude_cli_result_reports_the_decision_model_it_pinned_without_usage_info(monkeypatch):
    monkeypatch.setattr(RUN, RunRecorder(stdout="plain text"))
    provider = ClaudeCodeCLIProvider(cli_path="/usr/bin/claude", model="sonnet", decision_model="opus")
    assert provider.generate("p").model == "sonnet"
    assert provider.generate("p", tier="decision").model == "opus"


def test_null_provider_accepts_a_tier_and_reports_no_model():
    result = NullLLMProvider().generate("text", tier="decision")
    assert result.text == "text" and result.model is None


# --- generate_with_tier / generate_with_fallback --------------------------------


def test_generate_with_fallback_passes_the_tier_and_defaults_to_routine():
    spy = TierSpy()
    generate_with_fallback(spy, "p", "fallback")
    generate_with_fallback(spy, "p", "fallback", tier="decision")
    assert spy.tiers == ["routine", "decision"]


def test_a_provider_written_before_tiers_still_serves_every_routine_call():
    legacy = LegacyProvider()
    assert generate_with_fallback(legacy, "p", "fallback").text == "legacy answer"
    assert generate_with_tier(legacy, "p").text == "legacy answer"
    # ...but a decision call on one that cannot take a tier is loud, not silently downgraded.
    with pytest.raises(TypeError):
        generate_with_tier(legacy, "p", "decision")


def test_the_none_provider_falls_back_for_a_decision_call_too():
    result = generate_with_fallback(NullLLMProvider(), "p", "rule-based", tier="decision")
    assert result.text == "rule-based" and result.provider == "none"


# --- narratives are routine ----------------------------------------------------


def test_chart_insight_and_research_summary_use_the_routine_tier():
    from tests.test_analysis_service import FakeProvider as AnalysisData
    from tests.test_research_service import FakeResearchProvider

    spy = TierSpy()
    analysis_service.get_analysis("AAPL", AnalysisData(), spy)
    assert spy.tiers == ["routine"]

    spy = TierSpy()
    research_service.get_research("AAPL", FakeResearchProvider(), spy)
    assert spy.tiers == ["routine"]


# --- the overlay uses the decision tier ----------------------------------------


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _plan(session, monkeypatch, llm, overlay: bool):
    from tests.test_trade_plan_service import FakeUptrendDataProvider

    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="", ai_trading_overlay_enabled=overlay),
    )
    return trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)


def test_overlay_verdict_is_requested_on_the_decision_tier_and_the_take_on_the_routine_tier(session, monkeypatch):
    llm = TierSpy(
        text='{"stance": "bullish", "confidence": 80, "reasoning": "Strong setup."}',
        model_by_tier={"routine": "cheap-model", "decision": "strong-model"},
    )
    response = _plan(session, monkeypatch, llm, overlay=True)
    # one narrative (the plan's take) and one overlay verdict; they must not share a tier
    assert sorted(llm.tiers) == ["decision", "routine"]
    assert response.ai_opinion_stance == "bullish"
    assert response.ai_decision_model == "strong-model"
    assert response.ai_provider == "spy"


def test_overlay_off_makes_no_decision_call_and_records_no_decision_model(session, monkeypatch):
    llm = TierSpy(model_by_tier={"routine": "cheap-model", "decision": "strong-model"})
    response = _plan(session, monkeypatch, llm, overlay=False)
    assert llm.tiers == ["routine"]
    assert response.ai_decision_model is None


def test_decision_model_is_stored_on_the_plan_row(session, monkeypatch):
    from sqlmodel import select

    from app.portfolio.models import TradePlanRecord

    llm = TierSpy(
        text='{"stance": "bullish", "confidence": 80, "reasoning": "ok"}',
        model_by_tier={"decision": "strong-model"},
    )
    _plan(session, monkeypatch, llm, overlay=True)
    assert session.exec(select(TradePlanRecord)).first().ai_decision_model == "strong-model"


def test_a_failed_decision_call_leaves_the_decision_model_empty(session, monkeypatch):
    class DecisionFails(TierSpy):
        def generate(self, prompt, *, max_tokens=300, temperature=0.4, tier="routine"):
            if tier == "decision":
                self.tiers.append(tier)
                return LLMResult("", self.name, 1, error="boom")
            return super().generate(prompt, max_tokens=max_tokens, temperature=temperature, tier=tier)

    response = _plan(session, monkeypatch, DecisionFails(), overlay=True)
    assert response.ai_decision_model is None
    assert response.ai_opinion_stance is None


# --- settings validation -------------------------------------------------------


def test_every_decision_model_defaults_to_blank_so_existing_installs_are_unchanged():
    settings = AppSettings()
    for field in DECISION_FIELDS:
        assert getattr(settings, field) == ""
    # A settings.json written before the tiers existed loads with blanks.
    old = AppSettings.model_validate_json(json.dumps({"llm_provider": "openai", "openai_model": "gpt-4o-mini"}))
    assert all(getattr(old, field) == "" for field in DECISION_FIELDS)
    assert old.effective_decision_model() == "gpt-4o-mini"


@pytest.mark.parametrize("field", DECISION_FIELDS)
def test_blank_decision_model_is_accepted_and_trimmed(field):
    assert getattr(SettingsUpdateRequest(**{field: "  "}), field) == ""
    assert getattr(AppSettings(**{field: " "}), field) == ""
    assert getattr(SettingsUpdateRequest(), field) is None  # None = leave unchanged


@pytest.mark.parametrize(
    "field, value",
    [
        ("claude_cli_decision_model", "opus"),
        ("claude_cli_decision_model", "claude-opus-4-1-20250805"),
        ("openrouter_decision_model", "anthropic/claude-opus-4"),
        ("orcarouter_decision_model", "orcarouter/auto"),
        ("openai_decision_model", "gpt-4o"),
        ("gemini_decision_model", "gemini-2.5-pro"),
    ],
)
def test_valid_decision_models_are_accepted(field, value):
    assert getattr(SettingsUpdateRequest(**{field: f" {value} "}), field) == value
    assert getattr(AppSettings(**{field: value}), field) == value


BAD_MODELS = ["--model", "-x", "a b", "a;b", "$(whoami)", "a`b", "a\nb", "a&", "a?b", "a#b", "x" * 100]


@pytest.mark.parametrize("field", DECISION_FIELDS)
@pytest.mark.parametrize("value", BAD_MODELS)
def test_bad_decision_models_are_rejected_everywhere(field, value):
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(**{field: value})
    with pytest.raises(ValidationError):
        AppSettings(**{field: value})


@pytest.mark.parametrize("field", ["gemini_model", "gemini_decision_model"])
def test_gemini_models_cannot_hold_a_slash_because_they_become_a_url_path(field):
    for value in ("a/b", "../x", "models/x"):
        with pytest.raises(ValidationError):
            SettingsUpdateRequest(**{field: value})
    with pytest.raises(ValidationError):
        AppSettings(gemini_decision_model="a/b")


@pytest.mark.parametrize("field", ["openrouter_model", "orcarouter_model", "openai_model"])
def test_routine_http_models_are_checked_when_saved(field):
    assert getattr(SettingsUpdateRequest(**{field: "vendor/model:free"}), field) == "vendor/model:free"
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(**{field: "vendor model"})


def test_normalize_api_model_directly():
    assert normalize_api_model("  gpt-4o ") == "gpt-4o"
    assert normalize_api_model("") == ""
    with pytest.raises(ValueError):
        normalize_api_model("a/b", allow_slash=False)
    with pytest.raises(ValueError):
        normalize_api_model("a" * 97)


def test_put_settings_rejects_a_bad_decision_model_with_422():
    assert client.put("/api/settings", json={"openai_decision_model": "x; whoami"}).status_code == 422
    assert client.put("/api/settings", json={"gemini_decision_model": "a/b"}).status_code == 422
    assert client.put("/api/settings", json={"claude_cli_decision_model": "--x"}).status_code == 422


def test_put_settings_passes_decision_models_through(monkeypatch):
    seen: list[dict] = []

    def fake_update(**changes):
        seen.append(changes)
        return AppSettings(**changes)

    monkeypatch.setattr("app.api.routers.settings.update_app_settings", fake_update)
    resp = client.put("/api/settings", json={"claude_cli_decision_model": " opus ", "openai_decision_model": ""})
    assert resp.status_code == 200
    assert seen[0] == {"claude_cli_decision_model": "opus", "openai_decision_model": ""}


def test_get_settings_returns_the_decision_models(monkeypatch):
    monkeypatch.setattr(
        "app.api.routers.settings.load_app_settings", lambda: AppSettings(openai_decision_model="gpt-4o")
    )
    body = client.get("/api/settings").json()
    assert body["openai_decision_model"] == "gpt-4o"
    assert body["claude_cli_decision_model"] == ""


# --- status and test-connection -------------------------------------------------


def _status(monkeypatch, settings: AppSettings) -> dict:
    monkeypatch.setattr("app.api.routers.settings.load_app_settings", lambda: settings)
    monkeypatch.setattr("app.llm_providers.claude_code_cli_provider.shutil.which", lambda name: "/usr/bin/claude")
    return client.get("/api/settings/status").json()


def test_status_names_the_decision_model_only_when_it_differs(monkeypatch):
    base = dict(llm_provider="claude_code_cli", claude_cli_model="sonnet")
    assert _status(monkeypatch, AppSettings(**base))["ai_decision_model"] == ""
    assert _status(monkeypatch, AppSettings(**base, claude_cli_decision_model="sonnet"))["ai_decision_model"] == ""
    body = _status(monkeypatch, AppSettings(**base, claude_cli_decision_model="opus"))
    assert body["ai_decision_model"] == "opus"
    assert body["ai_model"] == "sonnet"


def test_status_has_no_decision_model_without_an_ai_provider(monkeypatch):
    assert _status(monkeypatch, AppSettings())["ai_decision_model"] == ""


def test_test_connection_request_defaults_to_the_routine_tier():
    assert settings_schemas.TestConnectionRequest(target="llm").tier == "routine"
    assert settings_schemas.TestConnectionRequest(target="llm", tier="decision").tier == "decision"
    with pytest.raises(ValidationError):
        settings_schemas.TestConnectionRequest(target="llm", tier="premium")


def _llm_test(monkeypatch, settings: AppSettings, payload: dict):
    monkeypatch.setattr("app.api.routers.settings.load_app_settings", lambda: settings)
    post = PostRecorder(_chat_payload(model="served-model"))
    monkeypatch.setattr(httpx, "post", post)
    return client.post("/api/settings/test-connection", json=payload).json(), post


def test_test_connection_tests_the_routine_model_by_default_and_names_it(monkeypatch):
    settings = AppSettings(llm_provider="openai", openai_api_key="k", openai_model="cheap", openai_decision_model="strong")
    body, post = _llm_test(monkeypatch, settings, {"target": "llm"})
    assert body["ok"] is True
    assert post.body["model"] == "cheap"
    assert "served-model" in body["message"]
    assert "decision" not in body["message"]


def test_test_connection_can_test_the_decision_model(monkeypatch):
    settings = AppSettings(llm_provider="openai", openai_api_key="k", openai_model="cheap", openai_decision_model="strong")
    body, post = _llm_test(monkeypatch, settings, {"target": "llm", "tier": "decision"})
    assert body["ok"] is True
    assert post.body["model"] == "strong"
    assert "decision model" in body["message"]
    assert "served-model" in body["message"]


def test_test_connection_says_when_there_is_no_separate_decision_model(monkeypatch):
    settings = AppSettings(llm_provider="openai", openai_api_key="k", openai_model="cheap")
    body, post = _llm_test(monkeypatch, settings, {"target": "llm", "tier": "decision"})
    assert post.body["model"] == "cheap"
    assert "no decision model set" in body["message"]


def test_the_two_tiers_have_separate_test_connection_cooldowns(monkeypatch):
    settings = AppSettings(llm_provider="openai", openai_api_key="k", openai_model="cheap", openai_decision_model="strong")
    first, _ = _llm_test(monkeypatch, settings, {"target": "llm"})
    second, _ = _llm_test(monkeypatch, settings, {"target": "llm", "tier": "decision"})
    assert first["ok"] is True and second["ok"] is True
    repeat = client.post("/api/settings/test-connection", json={"target": "llm", "tier": "decision"})
    assert repeat.status_code == 429


# --- strategy version -----------------------------------------------------------


def _fp(**settings) -> dict:
    return build_snapshot(AppSettings(**settings))


def test_overlay_fingerprint_follows_the_decision_model_while_the_overlay_is_on():
    on = dict(ai_trading_overlay_enabled=True)
    for provider, routine_field, decision_field, value in [
        ("claude_code_cli", "claude_cli_model", "claude_cli_decision_model", "opus"),
        ("openrouter", "openrouter_model", "openrouter_decision_model", "x/strong"),
        ("orcarouter", "orcarouter_model", "orcarouter_decision_model", "o/strong"),
        ("openai", "openai_model", "openai_decision_model", "gpt-strong"),
        ("gemini", "gemini_model", "gemini_decision_model", "gem-strong"),
    ]:
        base = _fp(**on, llm_provider=provider)
        with_decision = _fp(**on, llm_provider=provider, **{decision_field: value})
        assert with_decision != base  # setting a decision model mints a version
        assert with_decision["overlay"]["llm_model"] == value
        # once a decision model is set, the routine (narrative) model stops mattering
        assert _fp(**on, llm_provider=provider, **{decision_field: value, routine_field: "other-routine"}) == with_decision


def test_routine_model_alone_still_counts_while_no_decision_model_is_set():
    # With a blank decision model the verdict comes from the routine model, so changing it
    # changes the verdict's source: same behaviour as before the tiers existed.
    on = dict(ai_trading_overlay_enabled=True, llm_provider="claude_code_cli")
    assert _fp(**on, claude_cli_model="opus") != _fp(**on)


def test_decision_model_changes_never_bump_while_the_overlay_is_off():
    assert _fp(claude_cli_decision_model="opus", llm_provider="claude_code_cli") == _fp()
    assert _fp(openai_decision_model="gpt-5") == _fp()


def test_other_providers_decision_model_does_not_count():
    on = dict(ai_trading_overlay_enabled=True, llm_provider="openai")
    assert _fp(**on, gemini_decision_model="g", claude_cli_decision_model="opus") == _fp(**on)


def test_an_install_that_never_sets_a_decision_model_keeps_its_fingerprint():
    # The recorded overlay model is the routine model, exactly what it was recorded as before tiers.
    snap = _fp(ai_trading_overlay_enabled=True, llm_provider="openai", openai_model="gpt-4o-mini")
    assert snap["overlay"]["llm_model"] == "gpt-4o-mini"
    assert _fp(ai_trading_overlay_enabled=True, llm_provider="none")["overlay"]["llm_model"] is None


def test_change_text_names_the_overlay_model():
    on = dict(ai_trading_overlay_enabled=True, llm_provider="openai")
    before = _fp(**on)
    after = _fp(**on, openai_decision_model="gpt-5")
    assert "overlay LLM model gpt-4o-mini -> gpt-5" in describe_changes(before, after)
