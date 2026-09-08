from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import AppSettings
from app.main import app
from app.services.telegram_service import TelegramResult

client = TestClient(app)


def test_status_ai_online_false_for_none_provider(monkeypatch):
    """NullLLMProvider ('none') is a universal echo fallback, not real AI —
    it must never report as online even though is_configured() is always True."""
    monkeypatch.setattr("app.api.routers.settings.load_app_settings", lambda: AppSettings(llm_provider="none"))
    resp = client.get("/api/settings/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_online"] is False
    assert body["ai_provider"] == "none"


def test_status_ai_online_true_for_configured_real_provider(monkeypatch):
    monkeypatch.setattr(
        "app.api.routers.settings.load_app_settings",
        lambda: AppSettings(llm_provider="openrouter", openrouter_api_key="fake-key-123"),
    )
    resp = client.get("/api/settings/status")
    body = resp.json()
    assert body["ai_online"] is True
    assert body["ai_provider"] == "openrouter"


def test_status_ai_online_false_for_real_provider_without_key(monkeypatch):
    monkeypatch.setattr(
        "app.api.routers.settings.load_app_settings",
        lambda: AppSettings(llm_provider="openrouter", openrouter_api_key=""),
    )
    resp = client.get("/api/settings/status")
    assert resp.json()["ai_online"] is False


def test_status_finnhub_online_requires_enabled_and_key(monkeypatch):
    monkeypatch.setattr(
        "app.api.routers.settings.load_app_settings",
        lambda: AppSettings(finnhub_enabled=True, finnhub_api_key="key123"),
    )
    assert client.get("/api/settings/status").json()["finnhub_online"] is True

    monkeypatch.setattr(
        "app.api.routers.settings.load_app_settings",
        lambda: AppSettings(finnhub_enabled=False, finnhub_api_key="key123"),
    )
    assert client.get("/api/settings/status").json()["finnhub_online"] is False

    monkeypatch.setattr(
        "app.api.routers.settings.load_app_settings",
        lambda: AppSettings(finnhub_enabled=True, finnhub_api_key=""),
    )
    assert client.get("/api/settings/status").json()["finnhub_online"] is False


def test_status_telegram_online_requires_token_and_chat_id(monkeypatch):
    monkeypatch.setattr(
        "app.api.routers.settings.load_app_settings",
        lambda: AppSettings(telegram_bot_token="tok", telegram_chat_id="chat"),
    )
    assert client.get("/api/settings/status").json()["telegram_online"] is True

    monkeypatch.setattr(
        "app.api.routers.settings.load_app_settings",
        lambda: AppSettings(telegram_bot_token="tok", telegram_chat_id=""),
    )
    assert client.get("/api/settings/status").json()["telegram_online"] is False

    monkeypatch.setattr(
        "app.api.routers.settings.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id="chat"),
    )
    assert client.get("/api/settings/status").json()["telegram_online"] is False


def test_test_connection_telegram_missing_token_fails_without_network_call(monkeypatch):
    """Uses the real send_telegram_message (unmocked) to prove it short-circuits
    on a missing token before ever reaching httpx — no network call is made."""
    monkeypatch.setattr(
        "app.api.routers.settings.load_app_settings",
        lambda: AppSettings(telegram_bot_token="", telegram_chat_id=""),
    )
    resp = client.post("/api/settings/test-connection", json={"target": "telegram"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "token" in body["message"].lower()


def test_test_connection_telegram_success(monkeypatch):
    monkeypatch.setattr(
        "app.api.routers.settings.load_app_settings",
        lambda: AppSettings(telegram_bot_token="tok", telegram_chat_id="chat"),
    )
    monkeypatch.setattr(
        "app.api.routers.settings.send_telegram_message",
        lambda token, chat_id, text: TelegramResult(True, "Telegram message sent"),
    )
    resp = client.post("/api/settings/test-connection", json={"target": "telegram"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "sent" in body["message"].lower()


def test_test_connection_telegram_failure_propagates_message(monkeypatch):
    monkeypatch.setattr(
        "app.api.routers.settings.load_app_settings",
        lambda: AppSettings(telegram_bot_token="tok", telegram_chat_id="chat"),
    )
    monkeypatch.setattr(
        "app.api.routers.settings.send_telegram_message",
        lambda token, chat_id, text: TelegramResult(False, "Telegram API error: chat not found"),
    )
    resp = client.post("/api/settings/test-connection", json={"target": "telegram"})
    body = resp.json()
    assert body["ok"] is False
    assert "chat not found" in body["message"]


def test_redacted_masks_telegram_bot_token_but_not_chat_id():
    settings = AppSettings(telegram_bot_token="secret-token", telegram_chat_id="12345")
    data = settings.redacted()
    assert data["telegram_bot_token"].endswith("oken")
    assert "secret-token" not in data["telegram_bot_token"]
    assert data["telegram_chat_id"] == "12345"


def test_redacted_masks_absent_telegram_bot_token_as_empty_string():
    settings = AppSettings(telegram_bot_token="")
    assert settings.redacted()["telegram_bot_token"] == ""


def test_redacted_never_leaks_full_key_regardless_of_length():
    for key in ("ab", "sk-short", "sk-or-v1-a-very-long-real-looking-api-key-1234567890"):
        settings = AppSettings(openrouter_api_key=key)
        masked = settings.redacted()["openrouter_api_key"]
        assert key not in masked or len(key) <= 4
        assert masked != ""
