"""The one HTTP call the OpenAI-compatible providers (OpenAI, OpenRouter,
OrcaRouter) share, so structured output and its fallback exist in one place."""

from __future__ import annotations

import logging

import httpx

from app.llm_providers.structured import openai_response_format

logger = logging.getLogger(__name__)

# What a gateway answers when the chosen model cannot take `response_format`:
# 400 (bad request) or 422 (unprocessable). Any other failure (auth, rate limit,
# server error, network) is a real failure and is not retried here.
_SCHEMA_REJECTED_STATUSES = (400, 422)


def post_chat_completion(
    provider_name: str,
    url: str,
    api_key: str,
    payload: dict,
    *,
    response_schema: dict | None = None,
    schema_name: str = "response",
    timeout: float = 30,
) -> httpx.Response:
    """POST `payload` to a chat-completions endpoint and return the response
    (status already checked).

    With `response_schema` the request carries a `json_schema` response_format.
    OpenRouter and OrcaRouter pass it through only for models that support it,
    so when the API refuses it (400/422) the call is retried once without it
    and a warning records the degradation: the caller's lenient parser then
    reads the reply, which is the behaviour before structured output existed.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    if response_schema is None:
        resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp

    body = {**payload, "response_format": openai_response_format(response_schema, schema_name)}
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=timeout)
        resp.raise_for_status()
        return resp
    except httpx.HTTPStatusError as exc:
        if exc.response is None or exc.response.status_code not in _SCHEMA_REJECTED_STATUSES:
            raise
        logger.warning(
            "%s rejected structured output for model %s (HTTP %s); retrying once without it, "
            "so this reply will be read leniently.",
            provider_name, payload.get("model"), exc.response.status_code,
        )
    resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp
