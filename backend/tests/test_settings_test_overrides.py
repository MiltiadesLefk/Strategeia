"""The Settings test buttons test what is typed in the form, not just what is saved.

Real provider classes are used and only the HTTP layer is faked, so what is
checked is the key/model that actually goes out on the wire.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings, _mask_secret
from app.main import app
from app.services.telegram_service import TelegramResult
from app.settings_overrides import apply_test_overrides, effective_overrides

client = TestClient(app)

SAVED_KEY = "sk-or-SAVED-key-1111"
TYPED_KEY = "sk-or-TYPED-key-2222"


class _Resp:
    def __init__(self, payload=None):
        self._payload = payload or {"choices": [{"message": {"content": "OK"}}], "model": "m"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture
def saved(monkeypatch):
    settings = AppSettings(
        llm_provider="openrouter",
        openrouter_api_key=SAVED_KEY,
        openrouter_model="saved/model",
        finnhub_api_key="fh-SAVED-key",
        telegram_bot_token="tg-SAVED-token",
        telegram_chat_id="111",
    )
    monkeypatch.setattr("app.api.routers.settings.load_app_settings", lambda: settings)
    return settings


@pytest.fixture
def llm_http(monkeypatch):
    """Capture what the OpenRouter provider sends."""
    sent: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append({"auth": headers["Authorization"], "model": json["model"]})
        return _Resp()

    monkeypatch.setattr("app.llm_providers.openrouter_provider.httpx.post", fake_post)
    return sent


def test_no_overrides_tests_the_saved_values(saved, llm_http):
    body = client.post("/api/settings/test-connection", json={"target": "llm"}).json()
    assert body["ok"] is True
    assert llm_http == [{"auth": f"Bearer {SAVED_KEY}", "model": "saved/model"}]


def test_typed_key_and_model_are_used_instead_of_saved(saved, llm_http):
    resp = client.post(
        "/api/settings/test-connection",
        json={"target": "llm", "overrides": {"openrouter_api_key": TYPED_KEY, "openrouter_model": "typed/model"}},
    )
    assert resp.json()["ok"] is True
    assert llm_http == [{"auth": f"Bearer {TYPED_KEY}", "model": "typed/model"}]


def test_typed_provider_switches_the_provider_tested(saved, monkeypatch):
    seen = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append((url, headers["Authorization"], json["model"]))
        return _Resp()

    monkeypatch.setattr("app.llm_providers.openai_provider.httpx.post", fake_post)
    body = client.post(
        "/api/settings/test-connection",
        json={"target": "llm", "overrides": {"llm_provider": "openai", "openai_api_key": "sk-openai-typed-9999"}},
    ).json()
    assert body["ok"] is True
    assert seen[0][1] == "Bearer sk-openai-typed-9999"
    assert "openai" in seen[0][0]


def test_masked_hint_means_unchanged_so_the_saved_key_is_used(saved, llm_http):
    hint = _mask_secret(SAVED_KEY)
    assert "•" in hint
    client.post(
        "/api/settings/test-connection", json={"target": "llm", "overrides": {"openrouter_api_key": hint}}
    )
    assert llm_http[0]["auth"] == f"Bearer {SAVED_KEY}"


def test_blank_secret_provider_and_chat_id_mean_use_saved(saved, llm_http):
    client.post(
        "/api/settings/test-connection",
        json={"target": "llm", "overrides": {"openrouter_api_key": "  ", "llm_provider": ""}},
    )
    assert llm_http[0]["auth"] == f"Bearer {SAVED_KEY}"


def test_blank_model_is_tested_as_typed_not_replaced_by_saved(saved):
    """For a model name blank is a real choice (here: no decision model, so the
    decision tier answers with the routine model), not 'unchanged'."""
    changes = effective_overrides({"openrouter_decision_model": "", "openrouter_model": "x/y"})
    assert changes == {"openrouter_decision_model": "", "openrouter_model": "x/y"}
    assert apply_test_overrides(saved, {"openrouter_model": "x/y"}).openrouter_model == "x/y"


def test_overrides_are_never_saved(saved, llm_http, monkeypatch):
    persisted = []
    monkeypatch.setattr("app.config.save_app_settings", lambda s: persisted.append(s))
    monkeypatch.setattr("app.api.routers.settings.update_app_settings", lambda **kw: persisted.append(kw))
    client.post(
        "/api/settings/test-connection",
        json={"target": "llm", "overrides": {"openrouter_api_key": TYPED_KEY}},
    )
    assert persisted == []
    assert saved.openrouter_api_key == SAVED_KEY  # the saved object is untouched


def test_finnhub_uses_typed_key_and_skips_the_quote_cache(saved, monkeypatch):
    tokens = []

    def fake_get(url, params=None, timeout=None):
        tokens.append(params["token"])
        return _Resp({"c": 190.5})

    monkeypatch.setattr("app.data_providers.finnhub_provider.httpx.get", fake_get)
    body = client.post(
        "/api/settings/test-connection", json={"target": "finnhub", "overrides": {"finnhub_api_key": "fh-TYPED-key"}}
    ).json()
    assert body["ok"] is True
    assert tokens == ["fh-TYPED-key"]


def test_finnhub_rejected_key_is_reported_and_the_key_is_not_echoed(saved, monkeypatch):
    def fake_get(url, params=None, timeout=None):
        # httpx puts the full URL, token included, in its error text.
        raise httpx.HTTPError(f"401 for {url}?token={params['token']}")

    monkeypatch.setattr("app.data_providers.finnhub_provider.httpx.get", fake_get)
    resp = client.post(
        "/api/settings/test-connection", json={"target": "finnhub", "overrides": {"finnhub_api_key": "fh-TYPED-key"}}
    )
    body = resp.json()
    assert body["ok"] is False
    assert "fh-TYPED-key" not in resp.text


def test_telegram_uses_typed_token_and_chat_id(saved, monkeypatch):
    calls = []

    def fake_send(token, chat_id, text):
        calls.append((token, chat_id))
        return TelegramResult(True, "Telegram message sent")

    monkeypatch.setattr("app.api.routers.settings.send_telegram_message", fake_send)
    client.post(
        "/api/settings/test-connection",
        json={"target": "telegram", "overrides": {"telegram_bot_token": "tg-TYPED", "telegram_chat_id": "999"}},
    )
    assert calls == [("tg-TYPED", "999")]


def test_telegram_masked_token_with_typed_chat_id_uses_saved_token(saved, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.api.routers.settings.send_telegram_message",
        lambda token, chat_id, text: calls.append((token, chat_id)) or TelegramResult(True, "ok"),
    )
    client.post(
        "/api/settings/test-connection",
        json={
            "target": "telegram",
            "overrides": {"telegram_bot_token": _mask_secret("tg-SAVED-token"), "telegram_chat_id": "222"},
        },
    )
    assert calls == [("tg-SAVED-token", "222")]


def test_telegram_token_in_an_error_message_is_scrubbed(saved, monkeypatch):
    monkeypatch.setattr(
        "app.api.routers.settings.send_telegram_message",
        lambda token, chat_id, text: TelegramResult(False, f"Telegram request failed: 404 for /bot{token}/sendMessage"),
    )
    resp = client.post(
        "/api/settings/test-connection",
        json={"target": "telegram", "overrides": {"telegram_bot_token": "tg-TYPED-secret"}},
    )
    assert resp.json()["ok"] is False
    assert "tg-TYPED-secret" not in resp.text
    assert "[redacted]" in resp.json()["message"]


def test_secrets_never_appear_in_the_response_or_logs(saved, monkeypatch, caplog):
    def failing_post(url, headers=None, json=None, timeout=None):
        raise httpx.HTTPError(f"boom {headers['Authorization']}")

    monkeypatch.setattr("app.llm_providers.openrouter_provider.httpx.post", failing_post)
    with caplog.at_level(logging.DEBUG):
        resp = client.post(
            "/api/settings/test-connection",
            json={"target": "llm", "overrides": {"openrouter_api_key": TYPED_KEY}},
        )
    assert resp.json()["ok"] is False
    assert TYPED_KEY not in resp.text
    assert SAVED_KEY not in resp.text
    assert TYPED_KEY not in caplog.text
    assert SAVED_KEY not in caplog.text


def test_unknown_override_keys_are_rejected(saved):
    resp = client.post(
        "/api/settings/test-connection",
        json={"target": "llm", "overrides": {"openrouter_api_kee": TYPED_KEY}},
    )
    assert resp.status_code == 422
    # Not a setting the test buttons may override either.
    resp = client.post(
        "/api/settings/test-connection",
        json={"target": "llm", "overrides": {"paper_starting_cash": 5}},
    )
    assert resp.status_code == 422


def test_invalid_model_override_is_rejected_like_a_save(saved):
    resp = client.post(
        "/api/settings/test-connection",
        json={"target": "llm", "overrides": {"openrouter_model": "bad model name!"}},
    )
    assert resp.status_code == 422


def test_cooldown_still_applies_with_overrides(saved, llm_http):
    body = {"target": "llm", "overrides": {"openrouter_api_key": TYPED_KEY}}
    assert client.post("/api/settings/test-connection", json=body).status_code == 200
    again = client.post("/api/settings/test-connection", json=body)
    assert again.status_code == 429
    assert len(llm_http) == 1  # the second call never reached the provider


def test_put_settings_ignores_a_masked_secret_echoed_back(monkeypatch):
    """A client that sends the masked hint back means 'unchanged'; saving the
    hint over the real secret would silently break the key."""
    updates = []
    monkeypatch.setattr(
        "app.api.routers.settings.update_app_settings",
        lambda **changes: updates.append(changes) or AppSettings(),
    )
    client.put(
        "/api/settings",
        json={"openrouter_api_key": _mask_secret(SAVED_KEY), "openrouter_model": "a/b"},
    )
    assert updates == [{"openrouter_model": "a/b"}]
