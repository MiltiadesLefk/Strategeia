"""Research mode: AI calls made for research may search the web, but only when
the research_mode setting allows it, and never the AI overlay or the narration.

All HTTP is mocked and `subprocess.run` is faked, so no network is touched and
no Claude subscription usage is spent.
"""

from __future__ import annotations

import json
from itertools import product

import httpx
import pytest
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from app.config import AppSettings
from app.llm_providers.base import LLMResult, web_search_wanted
from app.llm_providers.claude_code_cli_provider import (
    RESEARCH_MAX_TURNS,
    RESEARCH_WEB_TOOLS,
    ClaudeCodeCLIProvider,
)
from app.llm_providers.factory import generate_with_fallback, generate_with_tier
from app.llm_providers.gemini_provider import GeminiProvider
from app.llm_providers.null_provider import NullLLMProvider
from app.llm_providers.openai_provider import OPENAI_RESPONSES_URL, OpenAIProvider, parse_responses_output
from app.llm_providers.openrouter_provider import OpenRouterProvider
from app.llm_providers.orcarouter_provider import OrcaRouterProvider
from app.llm_providers.research_mode import (
    RESEARCH_WEB_RULES,
    extract_urls,
    generate_for_research,
    research_tools_allowed,
    wrap_research_prompt,
)
from app.schemas.settings_schemas import SettingsUpdateRequest
from app.services import analysis_service, research_service, trade_plan_service
from app.strategy.snapshot import build_snapshot, fingerprint
from tests.test_llm_tiers import FakeCompletedProcess, PostRecorder, RunRecorder

RUN = "app.llm_providers.claude_code_cli_provider.subprocess.run"
WEB = AppSettings(research_mode="allow_web_search")
OFF = AppSettings(research_mode="our_data_only")


# --- the gate ------------------------------------------------------------------


@pytest.mark.parametrize(
    "purpose,mode,expected",
    [
        ("research", "allow_web_search", True),
        ("research", "our_data_only", False),
        ("narrate", "allow_web_search", False),
        ("narrate", "our_data_only", False),
        ("decide", "allow_web_search", False),
        ("decide", "our_data_only", False),
    ],
)
def test_the_gate_opens_only_for_research_with_web_search_allowed(purpose, mode, expected):
    assert research_tools_allowed(AppSettings(research_mode=mode), purpose) is expected


def test_a_provider_only_turns_the_web_on_for_a_cleared_research_call():
    for purpose, web in product(["narrate", "decide", "research"], [False, True]):
        assert web_search_wanted(purpose, web) is (purpose == "research" and web)


def test_research_mode_defaults_to_our_data_only_and_old_settings_files_load():
    assert AppSettings().research_mode == "our_data_only"
    # a settings.json written before the field existed
    assert AppSettings.model_validate({"llm_provider": "none", "min_confidence_for_trade": 30}).research_mode == (
        "our_data_only"
    )
    assert "research_mode" in AppSettings().redacted()


def test_research_mode_is_validated():
    with pytest.raises(ValidationError):
        AppSettings(research_mode="anything")
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(research_mode="anything")
    assert SettingsUpdateRequest(research_mode="allow_web_search").research_mode == "allow_web_search"
    assert SettingsUpdateRequest().research_mode is None  # None = leave unchanged


def test_research_mode_never_changes_the_strategy_fingerprint():
    assert fingerprint(build_snapshot(OFF)) == fingerprint(build_snapshot(WEB))


# --- prompt rules ----------------------------------------------------------------


def test_the_research_prompt_carries_the_web_content_rules():
    wrapped = wrap_research_prompt("Summarise NVDA.")
    assert wrapped.startswith("Summarise NVDA.")
    assert wrapped.endswith(RESEARCH_WEB_RULES)
    lowered = RESEARCH_WEB_RULES.lower()
    for phrase in ("untrusted", "never follow instructions", "cite", "inference", "not investment advice"):
        assert phrase in lowered


def test_extract_urls_dedupes_and_trims_punctuation():
    text = "See https://a.example/x, and (https://b.example/y). Again https://a.example/x."
    assert extract_urls(text) == ["https://a.example/x", "https://b.example/y"]


# --- Claude CLI argv -------------------------------------------------------------


def _cli(monkeypatch, stdout=None):
    rec = RunRecorder(stdout)
    monkeypatch.setattr(RUN, rec)
    return ClaudeCodeCLIProvider(cli_path="claude"), rec


def test_cli_default_call_keeps_no_tools_and_one_turn(monkeypatch):
    provider, rec = _cli(monkeypatch)
    provider.generate("hi")
    argv = rec.argv
    assert argv[argv.index("--allowedTools") + 1] == ""
    assert argv[argv.index("--max-turns") + 1] == "1"
    assert "--tools" not in argv
    assert not any("permission" in a for a in argv)


def test_cli_research_call_without_web_search_is_the_default_call(monkeypatch):
    provider, rec = _cli(monkeypatch)
    result = provider.generate("hi", purpose="research", web_search=False)
    assert "--tools" not in rec.argv and rec.argv[rec.argv.index("--allowedTools") + 1] == ""
    assert result.web_search_used is False


def test_cli_web_flag_on_a_non_research_purpose_is_ignored(monkeypatch):
    provider, rec = _cli(monkeypatch)
    for purpose in ("narrate", "decide"):
        provider.generate("hi", purpose=purpose, web_search=True)
        assert "--tools" not in rec.argv and rec.argv[rec.argv.index("--allowedTools") + 1] == ""


def test_cli_cleared_research_call_allows_only_the_two_web_tools(monkeypatch):
    stdout = json.dumps({"result": "Fact [src](https://example.com/a)."})
    provider, rec = _cli(monkeypatch, stdout)
    result = provider.generate("hi", purpose="research", web_search=True)
    argv = rec.argv
    assert RESEARCH_WEB_TOOLS == "WebSearch,WebFetch"
    assert argv[argv.index("--tools") + 1] == RESEARCH_WEB_TOOLS
    assert argv[argv.index("--allowedTools") + 1] == RESEARCH_WEB_TOOLS
    assert argv[argv.index("--max-turns") + 1] == RESEARCH_MAX_TURNS
    assert not any("permission" in a for a in argv)
    assert result.web_search_used is True
    assert result.sources == ["https://example.com/a"]


# --- HTTP providers: request bodies ----------------------------------------------


def _chat(monkeypatch, payload=None):
    rec = PostRecorder(payload or {"choices": [{"message": {"content": "hello"}}]})
    monkeypatch.setattr(httpx, "post", rec)
    return rec


def test_openrouter_adds_the_web_plugin_only_for_a_cleared_research_call(monkeypatch):
    rec = _chat(monkeypatch)
    provider = OpenRouterProvider("k", "m")
    provider.generate("p")
    assert "plugins" not in rec.body
    provider.generate("p", purpose="research")
    assert "plugins" not in rec.body
    provider.generate("p", purpose="narrate", web_search=True)
    assert "plugins" not in rec.body
    result = provider.generate("p", purpose="research", web_search=True)
    assert rec.body["plugins"][0]["id"] == "web"
    assert rec.body["model"] == "m"
    assert result.web_search_used is True


def test_openrouter_reports_url_citations_as_sources(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {
                    "content": "x",
                    "annotations": [
                        {"type": "url_citation", "url_citation": {"url": "https://a.example"}},
                        {"type": "url_citation", "url_citation": {"url": "https://a.example"}},
                        {"type": "other"},
                    ],
                }
            }
        ]
    }
    _chat(monkeypatch, payload)
    result = OpenRouterProvider("k", "m").generate("p", purpose="research", web_search=True)
    assert result.sources == ["https://a.example"]


def test_openai_plain_call_uses_chat_completions_and_research_uses_the_search_tool(monkeypatch):
    rec = _chat(monkeypatch)
    provider = OpenAIProvider("k", "gpt-x")
    plain = provider.generate("p", purpose="research")
    assert rec.url.endswith("/chat/completions") and "tools" not in rec.body
    assert plain.web_search_used is False

    responses = {
        "model": "gpt-x-2026",
        "output": [
            {"type": "web_search_call"},
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Answer.",
                        "annotations": [{"type": "url_citation", "url": "https://n.example"}],
                    }
                ],
            },
        ],
    }
    rec = _chat(monkeypatch, responses)
    result = provider.generate("p", purpose="research", web_search=True)
    assert rec.url == OPENAI_RESPONSES_URL
    assert rec.body["tools"] == [{"type": "web_search"}]
    assert rec.body["model"] == "gpt-x" and rec.body["input"] == "p"
    assert result.text == "Answer." and result.sources == ["https://n.example"] and result.web_search_used
    assert result.model == "gpt-x-2026"


def test_openai_web_call_with_no_text_is_an_error_not_an_empty_answer(monkeypatch):
    _chat(monkeypatch, {"output": []})
    result = OpenAIProvider("k", "gpt-x").generate("p", purpose="research", web_search=True)
    assert result.error and not result.web_search_used


def test_parse_responses_output_tolerates_odd_shapes():
    assert parse_responses_output(None) == ("", [])
    assert parse_responses_output({"output": ["x", {"type": "message", "content": None}]}) == ("", [])


def _gemini_payload(grounded: bool) -> dict:
    cand: dict = {"content": {"parts": [{"text": "hello"}]}}
    if grounded:
        cand["groundingMetadata"] = {"groundingChunks": [{"web": {"uri": "https://g.example"}}]}
    return {"candidates": [cand]}


def test_gemini_adds_google_search_only_for_a_cleared_research_call(monkeypatch):
    rec = _chat(monkeypatch, _gemini_payload(False))
    provider = GeminiProvider("k", "gemini-x")
    provider.generate("p", purpose="research")
    assert "tools" not in rec.calls[-1][1]["json"]
    provider.generate("p", purpose="decide", web_search=True)
    assert "tools" not in rec.calls[-1][1]["json"]
    rec = _chat(monkeypatch, _gemini_payload(True))
    result = provider.generate("p", purpose="research", web_search=True, response_schema={"type": "object"})
    body = rec.calls[-1][1]["json"]
    assert body["tools"] == [{"google_search": {}}]
    # search grounding and a JSON response schema cannot be combined
    assert "responseSchema" not in body["generationConfig"]
    assert result.web_search_used and result.sources == ["https://g.example"]


def test_orcarouter_has_no_web_search_and_sends_none(monkeypatch):
    rec = _chat(monkeypatch)
    provider = OrcaRouterProvider("k", "orcarouter/auto")
    assert provider.supports_web_search is False and provider.web_search_note
    result = provider.generate("p", purpose="research", web_search=True)
    assert "plugins" not in rec.body and "tools" not in rec.body
    assert result.web_search_used is False


# --- generate_for_research ---------------------------------------------------------


class RecordingProvider:
    name = "rec"
    supports_web_search = True
    web_search_note = "n"

    def __init__(self):
        self.calls: list[dict] = []

    def is_configured(self):
        return True

    def generate(self, prompt, *, max_tokens=300, temperature=0.4, tier="routine", **kwargs):
        self.calls.append({"prompt": prompt, "tier": tier, **kwargs})
        return LLMResult("text", self.name, 1)


def test_generate_for_research_requests_the_web_only_when_allowed():
    provider = RecordingProvider()
    generate_for_research(provider, "q", WEB)
    assert provider.calls[-1]["purpose"] == "research" and provider.calls[-1]["web_search"] is True
    assert provider.calls[-1]["prompt"].endswith(RESEARCH_WEB_RULES)
    generate_for_research(provider, "q", OFF)
    assert provider.calls[-1]["purpose"] == "research" and "web_search" not in provider.calls[-1]


def test_research_on_an_unsupported_provider_answers_from_our_data_and_says_so():
    provider = OrcaRouterProvider("", "m")
    provider.generate = lambda *a, **k: LLMResult("ok", "orcarouter", 1)  # type: ignore[method-assign]
    result = generate_for_research(provider, "q", WEB)
    assert result.web_search_used is False and result.note
    # with research mode off there is nothing to explain
    assert generate_for_research(provider, "q", OFF).note is None


def test_research_with_the_none_provider_notes_there_is_nothing_to_search_with():
    result = generate_for_research(NullLLMProvider(), "q", WEB)
    assert result.web_search_used is False and "No AI provider" in (result.note or "")


def test_llmresult_defaults_are_off():
    result = LLMResult("t", "p", 1)
    assert result.web_search_used is False and result.sources == [] and result.note is None
    assert LLMResult("t", "p", 1).sources is not LLMResult("t", "p", 1).sources  # not a shared list


def test_every_provider_declares_web_search_support():
    for provider, expected in [
        (ClaudeCodeCLIProvider(cli_path="claude"), True),
        (OpenRouterProvider("k"), True),
        (OpenAIProvider("k"), True),
        (GeminiProvider("k"), True),
        (OrcaRouterProvider("k"), False),
        (NullLLMProvider(), False),
    ]:
        assert provider.supports_web_search is expected
        assert provider.web_search_note


# --- existing call sites can never ask for research ----------------------------------


class PurposeSpy:
    """Fails the test if any call carries a purpose or a web flag at all."""

    name = "spy"

    def __init__(self, text="some text"):
        self.text = text
        self.calls = 0

    def is_configured(self):
        return True

    def generate(self, prompt, *, max_tokens=300, temperature=0.4, tier="routine", **kwargs):
        self.calls += 1
        assert "purpose" not in kwargs and "web_search" not in kwargs, kwargs
        return LLMResult(text=self.text, provider=self.name, latency_ms=1)


def test_the_helpers_existing_call_sites_use_cannot_pass_a_research_purpose():
    spy = PurposeSpy()
    generate_with_fallback(spy, "p", "fallback")
    generate_with_fallback(spy, "p", "fallback", tier="decision")
    generate_with_tier(spy, "p", "decision")
    assert spy.calls == 3
    with pytest.raises(TypeError):
        generate_with_fallback(spy, "p", "fallback", purpose="research")  # type: ignore[call-arg]


def test_narration_call_sites_never_pass_research():
    from tests.test_analysis_service import FakeProvider as AnalysisData
    from tests.test_research_service import FakeResearchProvider

    spy = PurposeSpy()
    analysis_service.get_analysis("AAPL", AnalysisData(), spy)
    research_service.get_research("AAPL", FakeResearchProvider(), spy)
    assert spy.calls == 2


def test_the_trade_plan_take_and_the_overlay_never_pass_research(monkeypatch):
    from tests.test_trade_plan_service import FakeUptrendDataProvider

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(
        "app.services.trade_plan_service.load_app_settings",
        # research mode is ON here: it must still not reach these calls
        lambda: AppSettings(
            telegram_bot_token="", telegram_chat_id="", ai_trading_overlay_enabled=True, research_mode="allow_web_search"
        ),
    )
    spy = PurposeSpy(text='{"stance": "bullish", "confidence": 80, "reasoning": "ok"}')
    with Session(engine) as session:
        trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), spy, session)
    assert spy.calls == 2  # the plan's take and the overlay verdict, neither with a purpose
