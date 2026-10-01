"""The AI Trading Overlay's reply: the form it has to fill in, and how a reply
is read back.

A provider that has a structured-output mode is handed `AI_OPINION_JSON_SCHEMA`
so the model can only answer in this shape. Even then the reply is checked
here, and a provider without such a mode (or a model that ignores it) still
gets a lenient read. Which of the three ways produced the opinion is recorded
on the plan (`parse_path`), because a model that keeps answering in prose is
a guardrail that is silently scoring 0, and that should be visible rather than
looking like "the AI had no objection".

Nothing here decides anything: it turns text into fields. What the fields are
allowed to do to a trade is in `ai_overlay_scoring`.

Idea from TradingAgents' structured-output schemas (Apache-2.0); the schema
and the three-way parse here are this project's own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# How an opinion was read.
#   structured - the whole reply was one JSON object that passed the schema.
#   lenient    - the reply was damaged (fenced, wrapped in prose, a field
#                missing or out of range) but a JSON object with a usable
#                stance or verdict could be pulled out of it.
#   failed     - nothing usable: no JSON object, or one with neither a stance
#                nor a verdict. The text is kept, no verdict is invented.
PARSE_STRUCTURED = "structured"
PARSE_LENIENT = "lenient"
PARSE_FAILED = "failed"
ParsePath = Literal["structured", "lenient", "failed"]


class AiOpinionSchema(BaseModel):
    """The overlay's answer. `extra="forbid"` is deliberate: it makes the JSON
    schema say `additionalProperties: false` (which strict structured-output
    modes require) and it means a reply carrying fields we never asked for is
    read leniently instead of being trusted as the form."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    stance: Literal["bullish", "bearish", "neutral"] = Field(
        description="Where you think the stock goes next. 'neutral' means no directional edge."
    )
    trade_verdict: Literal["take", "pass"] = Field(
        description="Would you actually take the specific trade the rule-based engine proposes?"
    )
    # Strict so that 80.5, "80" or true are not quietly accepted as the form;
    # the lenient path is where a sloppy number gets rounded and clamped.
    confidence: int = Field(
        strict=True, ge=0, le=100, description="Your own honest conviction, 0-100 (also your conviction in the verdict)."
    )
    reasoning: str = Field(
        min_length=1, description="2-3 plain-text sentences: agree or disagree with the rule-based verdict and the one main factor."
    )
    news_assessment: str = Field(
        min_length=1,
        description="1-2 plain-text sentences on the headlines, or 'No headlines available.' / 'No news coverage for this symbol.'",
    )


def ai_opinion_json_schema() -> dict:
    """The JSON schema handed to providers' structured-output modes. A fresh
    dict each call so a provider that edits it for its own dialect (see
    llm_providers.structured) cannot change what the next one sees."""
    return AiOpinionSchema.model_json_schema()


@dataclass
class ParsedAiOpinion:
    """The fields read from a reply, plus how they were read."""

    stance: str | None = None
    trade_verdict: str | None = None
    score: int | None = None
    text: str | None = None
    news_assessment: str | None = None
    parse_path: ParsePath = PARSE_FAILED


_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")


def _first_json_object(raw: str) -> dict | None:
    """The first `{...}` in `raw` that decodes to a JSON object: handles
    markdown fences and a sentence of prose before or after the object."""
    decoder = json.JSONDecoder()
    start = raw.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(raw, start)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return value
        start = raw.find("{", start + 1)
    return None


def _clean_str(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _lenient_score(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip().rstrip("%"))
        except ValueError:
            return None
    if isinstance(value, (int, float)) and value == value and abs(value) != float("inf"):
        return max(0, min(100, int(round(value))))
    return None


def parse_ai_opinion_reply(raw_text: str) -> ParsedAiOpinion:
    """Read the overlay's reply, strictest reading first. Never raises, and
    never turns an unreadable reply into a verdict: an unrecognised or absent
    `trade_verdict` stays None (the model has not said the trade is fine, and
    `overlay_opposes_trade` then falls back to the stance), exactly as before."""
    raw = (raw_text or "").strip()

    # 1. Strict: the whole reply is one JSON object that is the form.
    try:
        form = AiOpinionSchema.model_validate_json(raw, strict=True)
    except ValidationError:
        form = None
    if form is not None:
        return ParsedAiOpinion(
            stance=form.stance,
            trade_verdict=form.trade_verdict,
            score=form.confidence,
            text=form.reasoning,
            news_assessment=form.news_assessment,
            parse_path=PARSE_STRUCTURED,
        )

    # 2. Lenient: find a JSON object anywhere in the reply and take what is
    # usable from it, field by field.
    data = _first_json_object(_FENCE_RE.sub("", raw))
    if data is not None:
        stance = data.get("stance")
        stance = stance if stance in ("bullish", "bearish", "neutral") else None
        verdict = data.get("trade_verdict")
        verdict = verdict if verdict in ("take", "pass") else None
        parsed = ParsedAiOpinion(
            stance=stance,
            trade_verdict=verdict,
            score=_lenient_score(data.get("confidence")),
            text=_clean_str(data.get("reasoning")) or raw,
            news_assessment=_clean_str(data.get("news_assessment")),
            parse_path=PARSE_LENIENT,
        )
        # Neither field the guardrail keys off could be recovered: that is a
        # failed read, however JSON-shaped the rest was.
        if stance is not None or verdict is not None:
            return parsed
        parsed.parse_path = PARSE_FAILED
        return parsed

    # 3. Give up gracefully: keep the text, claim nothing.
    return ParsedAiOpinion(text=raw or None, parse_path=PARSE_FAILED)
