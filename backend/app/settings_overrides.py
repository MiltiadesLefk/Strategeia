"""Test a connection with what is typed in the Settings form, without saving it.

The settings API never returns a saved secret, only a masked hint such as
"••••ab12" (`AppSettings.redacted()`). The form therefore holds either a masked
hint (nothing typed), an empty string (nothing typed, no saved value), or a real
new value. A test must use the new value when there is one and the saved value
otherwise, and must never mistake the hint for a secret.
"""

from __future__ import annotations

from app.config import AppSettings

# Settings the test buttons can override, split by how "blank" is read. For a
# secret, the provider and the chat id, a blank value means "I typed nothing,
# use what is saved" (an empty key can never be a useful thing to test). A model
# name is different: blank is a real choice ("don't pin the CLI model", "decision
# tier uses the routine model") and testing it as typed is the honest result.
BLANK_MEANS_SAVED_FIELDS = frozenset(
    {
        "llm_provider",
        "openrouter_api_key",
        "orcarouter_api_key",
        "openai_api_key",
        "gemini_api_key",
        "finnhub_api_key",
        "telegram_bot_token",
        "telegram_chat_id",
    }
)
SECRET_FIELDS = frozenset(
    {
        "openrouter_api_key",
        "orcarouter_api_key",
        "openai_api_key",
        "gemini_api_key",
        "finnhub_api_key",
        "telegram_bot_token",
    }
)

# The bullet `config._mask_secret` pads a masked hint with. No real API key or
# bot token contains it, so its presence means "this is the hint the UI echoed
# back", not a secret someone typed.
MASK_CHARACTER = "•"


def is_masked_secret(value: str | None) -> bool:
    return bool(value) and MASK_CHARACTER in value


def effective_overrides(overrides: dict[str, str | None]) -> dict[str, str]:
    """The overrides that actually replace a saved value: not missing, not a
    blank where blank means "use saved", and not a masked hint."""
    out: dict[str, str] = {}
    for field, value in overrides.items():
        if value is None:
            continue
        if field in SECRET_FIELDS and is_masked_secret(value):
            continue
        if field in BLANK_MEANS_SAVED_FIELDS and not value.strip():
            continue
        out[field] = value.strip()
    return out


def apply_test_overrides(saved: AppSettings, overrides: dict[str, str | None]) -> AppSettings:
    """A temporary settings object: the saved settings with the typed values on
    top. Never written anywhere. Validated like a real settings load, so a bad
    value fails the same way it would on save."""
    changes = effective_overrides(overrides)
    if not changes:
        return saved
    return AppSettings.model_validate({**saved.model_dump(), **changes})


def scrub_secrets(text: str, secrets: list[str]) -> str:
    """Remove any secret that a provider or HTTP error echoed into its message
    (an httpx error carries the request URL, and Telegram's URL contains the
    bot token), so a test result never returns a key to the browser."""
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "[redacted]")
    return text
