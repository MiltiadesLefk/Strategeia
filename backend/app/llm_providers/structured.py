"""Structured output: asking a provider to answer in a given JSON schema.

`generate(..., response_schema=<JSON schema dict>)` is optional on every
provider. A provider with a structured-output mode uses it; one without it
(or a test double written before this existed) simply never sees the argument,
and the caller's lenient parser covers the gap. The schema only constrains the
shape of the answer, never what the answer may do: callers still validate the
reply themselves.

Each API accepts a slightly different dialect of JSON schema, so the schema a
caller passes is the plain one and the helpers here translate it.
"""

from __future__ import annotations

import copy
import inspect
from collections.abc import Callable

# Keywords that are descriptive noise to every structured-output API, or that
# Gemini's response schema rejects outright.
_NOISE_KEYS = frozenset({"title", "default", "$schema"})
_GEMINI_REJECTED_KEYS = frozenset({"additionalProperties"})


def _walk(node: object, drop: frozenset[str]) -> object:
    if isinstance(node, dict):
        out: dict = {}
        for key, value in node.items():
            # A property *named* "title" lives under "properties" and must stay;
            # only schema keywords are dropped, which is why "properties" is
            # walked value by value rather than as a keyword map.
            if key == "properties" and isinstance(value, dict):
                out[key] = {name: _walk(sub, drop) for name, sub in value.items()}
            elif key in drop:
                continue
            else:
                out[key] = _walk(value, drop)
        return out
    if isinstance(node, list):
        return [_walk(item, drop) for item in node]
    return node


def openai_response_format(schema: dict, name: str) -> dict:
    """The `response_format` body for OpenAI-compatible chat completions
    (OpenAI, OpenRouter, OrcaRouter). `strict` makes the model's output obey
    the schema exactly; the gateways pass it through for models that support
    it and reject it for the rest, which the providers handle by retrying
    without it."""
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": _walk(copy.deepcopy(schema), _NOISE_KEYS)},
    }


def gemini_response_schema(schema: dict) -> dict:
    """The schema for Gemini's `generationConfig.responseSchema`, which accepts
    only an OpenAPI-style subset: no `additionalProperties`, no titles or
    defaults. Types, enums, required, minimum/maximum and descriptions are
    kept."""
    return _walk(copy.deepcopy(schema), _NOISE_KEYS | _GEMINI_REJECTED_KEYS)  # type: ignore[return-value]


def accepts_response_schema(generate: Callable) -> bool:
    """Whether a provider's `generate` can take `response_schema`: it names
    the parameter or takes **kwargs. Used so a provider (or test double) that
    predates structured output keeps working instead of raising a TypeError on
    an argument that is only ever a hint."""
    try:
        params = inspect.signature(generate).parameters
    except (TypeError, ValueError):
        return False
    return "response_schema" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
