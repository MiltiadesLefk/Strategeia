"""Structured output for the AI overlay: the form, each provider's request, and
the three ways a reply is read (structured / lenient / failed)."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis.ai_opinion import (
    PARSE_FAILED,
    PARSE_LENIENT,
    PARSE_STRUCTURED,
    AiOpinionSchema,
    ai_opinion_json_schema,
    parse_ai_opinion_reply,
)
from app.config import AppSettings
from app.llm_providers import claude_code_cli_provider
from app.llm_providers.base import LLMResult
from app.llm_providers.claude_code_cli_provider import ClaudeCodeCLIProvider
from app.llm_providers.factory import generate_with_tier
from app.llm_providers.gemini_provider import GeminiProvider
from app.llm_providers.openai_provider import OpenAIProvider
from app.llm_providers.openrouter_provider import OpenRouterProvider
from app.llm_providers.orcarouter_provider import OrcaRouterProvider
from app.llm_providers.structured import accepts_response_schema, gemini_response_schema, openai_response_format
from app.portfolio.models import TradePlanRecord
from app.services import trade_plan_service
from tests.test_trade_plan_service import FakeUptrendDataProvider

GOOD = {
    "stance": "bullish",
    "trade_verdict": "take",
    "confidence": 72,
    "reasoning": "Agree with the long; trend and volume line up.",
    "news_assessment": "No headlines available.",
}


# --------------------------------------------------------------- the schema


def test_the_schema_accepts_a_complete_answer():
    form = AiOpinionSchema.model_validate(GOOD)
    assert (form.stance, form.trade_verdict, form.confidence) == ("bullish", "take", 72)


@pytest.mark.parametrize(
    "change",
    [
        {"stance": "sideways"},
        {"stance": "Bullish"},
        {"trade_verdict": "maybe"},
        {"trade_verdict": "buy"},
        {"confidence": 101},
        {"confidence": -1},
        {"confidence": 80.5},
        {"confidence": "80"},
        {"confidence": True},
        {"reasoning": ""},
        {"news_assessment": ""},
        {"reasoning": 5},
        {"surprise": "extra field"},
    ],
)
def test_the_schema_rejects_a_bad_answer(change):
    with pytest.raises(ValidationError):
        AiOpinionSchema.model_validate({**GOOD, **change})


@pytest.mark.parametrize("missing", list(GOOD))
def test_the_schema_requires_every_field(missing):
    data = {k: v for k, v in GOOD.items() if k != missing}
    with pytest.raises(ValidationError):
        AiOpinionSchema.model_validate(data)


def test_the_json_schema_is_closed_and_lists_every_field():
    schema = ai_opinion_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(GOOD)
    assert schema["properties"]["stance"]["enum"] == ["bullish", "bearish", "neutral"]
    assert schema["properties"]["trade_verdict"]["enum"] == ["take", "pass"]
    assert schema["properties"]["confidence"]["minimum"] == 0 and schema["properties"]["confidence"]["maximum"] == 100


def test_each_call_gets_its_own_copy_of_the_schema():
    first = ai_opinion_json_schema()
    first["properties"].pop("stance")
    assert "stance" in ai_opinion_json_schema()["properties"]


# -------------------------------------------------- dialect translation


def test_openai_format_is_strict_json_schema_without_noise():
    fmt = openai_response_format(ai_opinion_json_schema(), "x")
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert "title" not in schema and schema["additionalProperties"] is False
    assert "title" not in schema["properties"]["stance"]


def test_gemini_schema_drops_what_gemini_rejects_and_keeps_the_rest():
    schema = gemini_response_schema(ai_opinion_json_schema())
    assert "additionalProperties" not in schema and "title" not in schema
    assert schema["properties"]["stance"]["enum"] == ["bullish", "bearish", "neutral"]
    assert schema["properties"]["confidence"]["maximum"] == 100
    assert set(schema["required"]) == set(GOOD)


def test_a_property_called_title_survives_translation():
    schema = {"type": "object", "properties": {"title": {"type": "string", "title": "Title"}}, "title": "T"}
    out = gemini_response_schema(schema)
    assert "title" in out["properties"] and "title" not in out["properties"]["title"] and "title" not in out


def test_accepts_response_schema_looks_at_the_signature():
    def plain(prompt, *, max_tokens=1): ...
    def named(prompt, *, response_schema=None): ...
    def varkw(prompt, **kwargs): ...

    assert not accepts_response_schema(plain)
    assert accepts_response_schema(named)
    assert accepts_response_schema(varkw)


# --------------------------------------------- generate_with_tier plumbing


class SchemaRecorder:
    name = "rec"

    def __init__(self):
        self.calls: list[dict] = []

    def is_configured(self):
        return True

    def generate(self, prompt, *, max_tokens=300, temperature=0.4, tier="routine", response_schema=None):
        self.calls.append({"tier": tier, "schema": response_schema})
        return LLMResult("ok", self.name, 1)


class NoSchemaProvider:
    name = "old"

    def is_configured(self):
        return True

    def generate(self, prompt, *, max_tokens=300, temperature=0.4, tier="routine"):
        return LLMResult("ok", self.name, 1)


def test_generate_with_tier_passes_the_schema_to_a_provider_that_takes_it():
    rec = SchemaRecorder()
    schema = {"type": "object"}
    generate_with_tier(rec, "p", "decision", response_schema=schema)
    generate_with_tier(rec, "p")
    assert rec.calls == [{"tier": "decision", "schema": schema}, {"tier": "routine", "schema": None}]


def test_generate_with_tier_still_works_for_a_provider_without_the_parameter():
    assert generate_with_tier(NoSchemaProvider(), "p", "decision", response_schema={"type": "object"}).text == "ok"


# ------------------------------------------------ provider request bodies


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=httpx.Request("POST", "http://x"), response=self)

    def json(self):
        return self._payload


class Poster:
    """httpx.post stand-in answering from a list of statuses, remembering bodies."""

    def __init__(self, payload, statuses=(200,)):
        self.payload = payload
        self.statuses = list(statuses)
        self.bodies: list[dict] = []

    def __call__(self, url, **kwargs):
        self.bodies.append(kwargs["json"])
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return FakeResponse(self.payload, status)


CHAT = {"choices": [{"message": {"content": json.dumps(GOOD)}}]}
GEMINI = {"candidates": [{"content": {"parts": [{"text": json.dumps(GOOD)}]}}]}


@pytest.mark.parametrize("provider_cls", [OpenAIProvider, OpenRouterProvider, OrcaRouterProvider])
def test_openai_style_providers_send_the_schema_as_response_format(monkeypatch, provider_cls):
    post = Poster(CHAT)
    monkeypatch.setattr(httpx, "post", post)

    result = provider_cls("key", "m").generate("p", response_schema=ai_opinion_json_schema())

    fmt = post.bodies[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["required"]
    assert result.text == json.dumps(GOOD)


@pytest.mark.parametrize("provider_cls", [OpenAIProvider, OpenRouterProvider, OrcaRouterProvider])
def test_no_schema_means_no_response_format(monkeypatch, provider_cls):
    post = Poster(CHAT)
    monkeypatch.setattr(httpx, "post", post)
    provider_cls("key", "m").generate("p")
    assert "response_format" not in post.bodies[0]


@pytest.mark.parametrize("status", [400, 422])
def test_a_gateway_that_rejects_the_schema_is_retried_once_without_it(monkeypatch, caplog, status):
    post = Poster(CHAT, statuses=(status, 200))
    monkeypatch.setattr(httpx, "post", post)

    with caplog.at_level(logging.WARNING):
        result = OpenRouterProvider("key", "some/model").generate("p", response_schema=ai_opinion_json_schema())

    assert result.error is None and result.text == json.dumps(GOOD)
    assert len(post.bodies) == 2
    assert "response_format" in post.bodies[0] and "response_format" not in post.bodies[1]
    assert "rejected structured output" in caplog.text


def test_other_failures_are_not_retried(monkeypatch):
    post = Poster(CHAT, statuses=(401,))
    monkeypatch.setattr(httpx, "post", post)
    result = OpenAIProvider("key", "m").generate("p", response_schema=ai_opinion_json_schema())
    assert result.error and len(post.bodies) == 1


def test_a_rejection_without_a_schema_is_just_an_error(monkeypatch):
    post = Poster(CHAT, statuses=(400,))
    monkeypatch.setattr(httpx, "post", post)
    assert OpenAIProvider("key", "m").generate("p").error
    assert len(post.bodies) == 1


def test_gemini_sends_json_mime_type_and_a_translated_schema(monkeypatch):
    post = Poster(GEMINI)
    monkeypatch.setattr(httpx, "post", post)

    result = GeminiProvider("key", "gemini-x").generate("p", response_schema=ai_opinion_json_schema())

    config = post.bodies[0]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert "additionalProperties" not in config["responseSchema"]
    assert config["responseSchema"]["properties"]["trade_verdict"]["enum"] == ["take", "pass"]
    assert config["maxOutputTokens"] == 300
    assert result.text == json.dumps(GOOD)


def test_gemini_without_a_schema_sends_neither_key(monkeypatch):
    post = Poster(GEMINI)
    monkeypatch.setattr(httpx, "post", post)
    GeminiProvider("key", "gemini-x").generate("p")
    config = post.bodies[0]["generationConfig"]
    assert "responseMimeType" not in config and "responseSchema" not in config


def test_gemini_retries_once_without_the_schema_on_a_400(monkeypatch):
    post = Poster(GEMINI, statuses=(400, 200))
    monkeypatch.setattr(httpx, "post", post)
    result = GeminiProvider("key", "gemini-x").generate("p", response_schema=ai_opinion_json_schema())
    assert result.error is None
    assert "responseSchema" in post.bodies[0]["generationConfig"]
    assert "responseSchema" not in post.bodies[1]["generationConfig"]


# ------------------------------------------------------------- Claude CLI


class FakeProc:
    def __init__(self, stdout):
        self.returncode, self.stdout, self.stderr = 0, stdout, ""


class RunRecorder:
    def __init__(self, stdout):
        self.stdout = stdout
        self.argv: list[str] = []

    def __call__(self, argv, **kwargs):
        self.argv = list(argv)
        return FakeProc(self.stdout)


def _cli():
    return ClaudeCodeCLIProvider(cli_path="claude")


def test_cli_passes_the_schema_as_json_schema_and_reads_the_structured_field(monkeypatch):
    envelope = {"result": "", "structured_output": GOOD, "modelUsage": {"m": {"costUSD": 1.0}}}
    run = RunRecorder(json.dumps(envelope))
    monkeypatch.setattr(claude_code_cli_provider.subprocess, "run", run)
    schema = ai_opinion_json_schema()

    result = _cli().generate("p", response_schema=schema)

    assert run.argv[run.argv.index("--json-schema") + 1] == json.dumps(schema, separators=(",", ":"))
    assert "--allowedTools" in run.argv and "--max-turns" in run.argv  # still a no-tools, single-turn call
    assert json.loads(result.text) == GOOD
    assert result.model == "m"


def test_cli_falls_back_to_result_text_when_there_is_no_structured_field(monkeypatch):
    run = RunRecorder(json.dumps({"result": json.dumps(GOOD)}))
    monkeypatch.setattr(claude_code_cli_provider.subprocess, "run", run)
    assert json.loads(_cli().generate("p", response_schema=ai_opinion_json_schema()).text) == GOOD


def test_cli_without_a_schema_has_no_flag_and_ignores_structured_output(monkeypatch):
    run = RunRecorder(json.dumps({"result": "plain text", "structured_output": GOOD}))
    monkeypatch.setattr(claude_code_cli_provider.subprocess, "run", run)
    result = _cli().generate("p")
    assert "--json-schema" not in run.argv
    assert result.text == "plain text"


# ------------------------------------------------------------ parse paths


def test_a_valid_single_object_is_the_structured_path():
    parsed = parse_ai_opinion_reply(json.dumps(GOOD))
    assert parsed.parse_path == PARSE_STRUCTURED
    assert (parsed.stance, parsed.trade_verdict, parsed.score) == ("bullish", "take", 72)
    assert parsed.text == GOOD["reasoning"] and parsed.news_assessment == GOOD["news_assessment"]


def test_surrounding_whitespace_is_still_structured():
    assert parse_ai_opinion_reply("\n  " + json.dumps(GOOD) + "\n").parse_path == PARSE_STRUCTURED


@pytest.mark.parametrize(
    "reply",
    [
        "```json\n" + json.dumps(GOOD) + "\n```",
        "Here is my opinion: " + json.dumps(GOOD) + " Hope that helps.",
        json.dumps({**GOOD, "extra": 1}),
        json.dumps({**GOOD, "confidence": 72.4}),
    ],
)
def test_a_damaged_reply_with_a_usable_object_is_the_lenient_path(reply):
    parsed = parse_ai_opinion_reply(reply)
    assert parsed.parse_path == PARSE_LENIENT
    assert (parsed.stance, parsed.trade_verdict, parsed.score) == ("bullish", "take", 72)
    assert parsed.text == GOOD["reasoning"]


def test_lenient_keeps_what_is_usable_and_never_invents_a_verdict():
    parsed = parse_ai_opinion_reply('{"stance": "bearish", "trade_verdict": "maybe", "confidence": 150, "reasoning": "Unsure."}')
    assert parsed.parse_path == PARSE_LENIENT
    assert parsed.trade_verdict is None  # "maybe" is not approval
    assert parsed.stance == "bearish"
    assert parsed.score == 100  # clamped
    assert parsed.news_assessment is None


def test_a_missing_verdict_is_lenient_not_failed_while_the_stance_is_usable():
    parsed = parse_ai_opinion_reply('{"stance": "bearish", "confidence": 78, "reasoning": "Exhausted."}')
    assert parsed.parse_path == PARSE_LENIENT and parsed.trade_verdict is None and parsed.stance == "bearish"


@pytest.mark.parametrize(
    "reply",
    [
        "I think this looks bullish overall, roughly 70% confidence.",
        "",
        "{not json at all",
        '{"confidence": 80, "reasoning": "No stance or verdict given."}',
        '{"stance": "up", "trade_verdict": "yes"}',
        "[1, 2, 3]",
    ],
)
def test_an_unreadable_reply_is_failed_and_claims_nothing(reply):
    parsed = parse_ai_opinion_reply(reply)
    assert parsed.parse_path == PARSE_FAILED
    assert parsed.stance is None and parsed.trade_verdict is None


def test_a_failed_reply_keeps_its_text():
    assert parse_ai_opinion_reply("Looks bullish to me.").text == "Looks bullish to me."


def test_the_service_parser_carries_the_path():
    assert trade_plan_service._parse_ai_opinion(json.dumps(GOOD)).parse_path == PARSE_STRUCTURED
    assert trade_plan_service._parse_ai_opinion("prose").parse_path == PARSE_FAILED


# ------------------------------------------------- through the service


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class SchemaAwareLLM:
    """A configured provider that takes response_schema and records it."""

    name = "fake-llm"

    def __init__(self, overlay_reply):
        self.overlay_reply = overlay_reply
        self.schemas: list[dict | None] = []

    def is_configured(self):
        return True

    def generate(self, prompt, *, max_tokens=300, temperature=0.4, tier="routine", response_schema=None):
        if tier == "decision":
            self.schemas.append(response_schema)
            return LLMResult(self.overlay_reply, self.name, 1, model="m1")
        return LLMResult("a take", self.name, 1)


def _settings(monkeypatch, **overrides):
    values = dict(telegram_bot_token="", telegram_chat_id="", ai_trading_overlay_enabled=True, auto_execute_trade_plans=False)
    values.update(overrides)
    monkeypatch.setattr("app.services.trade_plan_service.load_app_settings", lambda: AppSettings(**values))


def _generate(session, llm):
    return trade_plan_service.generate_trade_plan("AAPL", 100_000.0, 1.0, FakeUptrendDataProvider(), llm, session)


def test_the_overlay_asks_for_structured_output_and_records_the_path(session, monkeypatch):
    _settings(monkeypatch)
    llm = SchemaAwareLLM(json.dumps({**GOOD, "trade_verdict": "take"}))

    response = _generate(session, llm)

    assert llm.schemas == [ai_opinion_json_schema()]
    assert response.ai_opinion_parse == "structured"
    assert session.exec(select(TradePlanRecord)).first().ai_opinion_parse == "structured"


def test_a_damaged_reply_is_recorded_as_lenient(session, monkeypatch):
    _settings(monkeypatch)
    response = _generate(session, SchemaAwareLLM("```json\n" + json.dumps(GOOD) + "\n```"))
    assert response.ai_opinion_parse == "lenient"
    assert response.ai_trade_verdict == "take"


def test_an_unreadable_reply_is_logged_recorded_and_never_a_verdict(session, monkeypatch, caplog):
    _settings(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="app.services.trade_plan_service"):
        response = _generate(session, SchemaAwareLLM("I would probably pass on this one."))

    assert response.ai_opinion_parse == "failed"
    assert response.ai_trade_verdict is None and response.ai_opinion_stance is None
    assert response.ai_opinion_text == "I would probably pass on this one."
    assert response.ai_overlay_score == 0  # no objection could be recorded, and none was made up
    assert response.direction == "long"
    assert "could not be read" in caplog.text
    assert session.exec(select(TradePlanRecord)).first().ai_opinion_parse == "failed"


def test_the_parse_path_never_changes_the_confidence_math(session, monkeypatch):
    _settings(monkeypatch)
    objection = {**GOOD, "stance": "bearish", "trade_verdict": "pass", "confidence": 80}
    structured = _generate(session, SchemaAwareLLM(json.dumps(objection)))
    lenient = _generate(session, SchemaAwareLLM("```json\n" + json.dumps(objection) + "\n```"))

    assert (structured.ai_opinion_parse, lenient.ai_opinion_parse) == ("structured", "lenient")
    assert structured.confidence_score == lenient.confidence_score
    assert structured.ai_overlay_score == lenient.ai_overlay_score < 0


def test_overlay_off_records_no_parse_path(session, monkeypatch):
    _settings(monkeypatch, ai_trading_overlay_enabled=False)
    llm = SchemaAwareLLM(json.dumps(GOOD))
    response = _generate(session, llm)
    assert response.ai_opinion_parse is None and llm.schemas == []
