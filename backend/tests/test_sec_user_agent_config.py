"""SEC_EDGAR_USER_AGENT: the contact SEC EDGAR sees on every insider-trade
request (plan.md F-7).

docker-compose.yml passes SEC_EDGAR_USER_AGENT=${SEC_EDGAR_USER_AGENT:-} into
the backend, so a deployment that never set it still delivers the variable,
as an empty string — and pydantic-settings reads "" as the value "", not as
"absent". InfraSettings' validator turns blank back into the placeholder,
which plan.md D13 keeps on purpose for now.

Every test builds a fresh InfraSettings(_env_file=None): the process
environment only, never the lru_cached get_infra_settings() and never this
machine's own backend/.env. pydantic-settings falls back to that .env FILE
whenever the OS variable is absent (see test_shared_secret_generation.py), so
without _env_file=None a developer's real local value could leak into these
results.
"""

from __future__ import annotations

import logging

import pytest

from app.config import SEC_EDGAR_PLACEHOLDER_USER_AGENT, InfraSettings
from app.data_providers import sec_edgar_provider

ENV_VAR = "SEC_EDGAR_USER_AGENT"
CUSTOM = "Strategeia/1.0 (personal research; someone@example.org)"


def _infra() -> InfraSettings:
    return InfraSettings(_env_file=None)


def _warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "app.config" and r.levelno == logging.WARNING]


def test_unset_uses_the_placeholder(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert _infra().sec_edgar_user_agent == SEC_EDGAR_PLACEHOLDER_USER_AGENT


def test_the_compose_passthrough_left_blank_falls_back_to_the_placeholder(monkeypatch):
    """The Docker default: nothing in the root .env, so compose substitutes
    "" — which must not become an empty User-Agent (EDGAR refuses requests
    that don't say who sent them)."""
    monkeypatch.setenv(ENV_VAR, "")
    assert _infra().sec_edgar_user_agent == SEC_EDGAR_PLACEHOLDER_USER_AGENT


@pytest.mark.parametrize("blank", [" ", "   ", "\t", "\n", " \r\n "])
def test_whitespace_only_falls_back_to_the_placeholder(monkeypatch, blank):
    monkeypatch.setenv(ENV_VAR, blank)
    assert _infra().sec_edgar_user_agent == SEC_EDGAR_PLACEHOLDER_USER_AGENT


def test_a_custom_value_is_used(monkeypatch):
    monkeypatch.setenv(ENV_VAR, CUSTOM)
    assert _infra().sec_edgar_user_agent == CUSTOM


def test_surrounding_whitespace_is_stripped(monkeypatch):
    monkeypatch.setenv(ENV_VAR, f"  {CUSTOM}\r\n")
    assert _infra().sec_edgar_user_agent == CUSTOM


def test_a_line_break_inside_collapses_to_one_space(monkeypatch):
    """http.client raises ValueError on a header value with a line break in
    it, and sec_edgar_provider._fetch only converts network errors."""
    monkeypatch.setenv(ENV_VAR, "Strategeia/1.0 (personal research;\n   someone@example.org)")
    assert _infra().sec_edgar_user_agent == CUSTOM


def test_the_configured_value_is_what_edgar_receives(monkeypatch):
    monkeypatch.setenv(ENV_VAR, CUSTOM)
    infra = _infra()
    monkeypatch.setattr(sec_edgar_provider, "get_infra_settings", lambda: infra)
    assert sec_edgar_provider._headers()["User-Agent"] == CUSTOM


def test_a_blank_compose_value_still_sends_the_placeholder(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "")
    infra = _infra()
    monkeypatch.setattr(sec_edgar_provider, "get_infra_settings", lambda: infra)
    assert sec_edgar_provider._headers()["User-Agent"] == SEC_EDGAR_PLACEHOLDER_USER_AGENT


@pytest.mark.parametrize(
    "value, fragment",
    [
        ("Strategeia/1.0 (+https://github.com/someone/strategeia; someone@example.org)",
         "https://github.com/someone/strategeia"),
        ("Strategeia/1.0 (see http://localhost:8000; someone@example.org)", "http://localhost:8000"),
        ("Strategeia/1.0 (www.example.org; someone@example.org)", "www.example.org"),
        ("Strategeia/1.0 (github.com/someone/strategeia; someone@example.org)", "github.com/someone/strategeia"),
        ("Strategeia/1.0 (github.com; someone@example.org)", "github.com"),
    ],
)
def test_a_web_address_logs_one_warning_and_is_still_used(monkeypatch, caplog, value, fragment):
    """Warn, never replace: the operator's own contact is still the right
    thing to send, and the check is a pattern that can misfire."""
    monkeypatch.setenv(ENV_VAR, value)
    with caplog.at_level(logging.WARNING, logger="app.config"):
        infra = _infra()

    assert infra.sec_edgar_user_agent == value
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "SEC_EDGAR_USER_AGENT" in message
    assert "403" in message
    assert repr(fragment) in message
    # The contact half never reaches the log: it's someone's real address.
    assert "someone@example.org" not in message


@pytest.mark.parametrize(
    "value",
    [
        SEC_EDGAR_PLACEHOLDER_USER_AGENT,  # the deliberate default (D13): no nagging
        "Strategeia/1.0 (personal research; first.last@example.org)",
        "Jane Q. Doe jane.doe+sec@mail.example.co.uk",
        "Strategeia/1.0 (Python 3.14, build 1.0.2/7; someone@example.org)",
        "Strategeia/1.0 (research, e.g. insider data; someone@example.org)",
        "MyHttpClient/1.0 (research; someone@example.org)",
    ],
)
def test_email_addresses_versions_and_the_placeholder_do_not_warn(monkeypatch, caplog, value):
    monkeypatch.setenv(ENV_VAR, value)
    with caplog.at_level(logging.WARNING, logger="app.config"):
        infra = _infra()

    assert infra.sec_edgar_user_agent == value
    assert _warnings(caplog) == []


@pytest.mark.parametrize(
    "value",
    [
        "Μιλτιάδης/1.0 (έρευνα; someone@example.org)",  # Greek: http.client can't encode it
        "José/1.0 (research; someone@example.org)",  # Latin-1, but not ASCII
        "Name/1.0 (research\x07; someone@example.org)",  # a control character
    ],
)
def test_a_value_that_cannot_be_a_header_falls_back_with_a_warning(monkeypatch, caplog, value):
    """Sent as-is, a non-Latin-1 user-agent raises UnicodeEncodeError inside
    http.client — a ValueError that _fetch doesn't catch, so it would fail
    every trade-plan evaluation of a US stock instead of just costing the
    insider signal."""
    monkeypatch.setenv(ENV_VAR, value)
    with caplog.at_level(logging.WARNING, logger="app.config"):
        infra = _infra()

    assert infra.sec_edgar_user_agent == SEC_EDGAR_PLACEHOLDER_USER_AGENT
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "placeholder" in warnings[0].getMessage()
    assert "someone@example.org" not in warnings[0].getMessage()


def test_whatever_is_configured_the_result_is_a_sendable_header(monkeypatch):
    """The property the fallbacks exist for: every value the setting can
    end up holding encodes as an HTTP header without raising."""
    for value in ["", " \n ", CUSTOM, "Μιλτιάδης/1.0 (a@b.example)", "a\r\nInjected: header", "x\x1by"]:
        monkeypatch.setenv(ENV_VAR, value)
        result = _infra().sec_edgar_user_agent
        assert result
        assert result.isascii() and result.isprintable()
        result.encode("latin-1")  # what http.client does; must not raise
