from __future__ import annotations

import httpx
import pytest

from app.services.telegram_service import notify_trade_plan, send_message


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"ok": True}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        return self._payload


def test_send_message_missing_token_is_a_no_network_failure(monkeypatch):
    def _fail_if_called(*a, **k):
        raise AssertionError("must not hit the network with no token configured")

    monkeypatch.setattr("app.services.telegram_service.httpx.post", _fail_if_called)
    result = send_message("", "chat123", "hello")
    assert result.ok is False
    assert "token" in result.message.lower()


def test_send_message_missing_chat_id_is_a_no_network_failure(monkeypatch):
    def _fail_if_called(*a, **k):
        raise AssertionError("must not hit the network with no chat id configured")

    monkeypatch.setattr("app.services.telegram_service.httpx.post", _fail_if_called)
    result = send_message("tok", "", "hello")
    assert result.ok is False
    assert "chat id" in result.message.lower()


def test_send_message_success(monkeypatch):
    monkeypatch.setattr(
        "app.services.telegram_service.httpx.post",
        lambda url, json, timeout: FakeResponse(200, {"ok": True}),
    )
    result = send_message("tok", "chat123", "hello")
    assert result.ok is True


def test_send_message_api_error_response(monkeypatch):
    monkeypatch.setattr(
        "app.services.telegram_service.httpx.post",
        lambda url, json, timeout: FakeResponse(200, {"ok": False, "description": "chat not found"}),
    )
    result = send_message("tok", "badchat", "hello")
    assert result.ok is False
    assert "chat not found" in result.message


def test_send_message_network_error_is_caught(monkeypatch):
    def _raise(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("app.services.telegram_service.httpx.post", _raise)
    result = send_message("tok", "chat123", "hello")
    assert result.ok is False
    assert "failed" in result.message.lower()


def test_notify_trade_plan_silently_skips_when_unconfigured(monkeypatch):
    def _fail_if_called(*a, **k):
        raise AssertionError("must not hit the network when telegram isn't configured")

    monkeypatch.setattr("app.services.telegram_service.httpx.post", _fail_if_called)
    # Must not raise even though nothing is configured.
    notify_trade_plan("", "", "New trade plan: AAPL LONG")


def test_notify_trade_plan_swallows_api_errors(monkeypatch):
    monkeypatch.setattr(
        "app.services.telegram_service.httpx.post",
        lambda url, json, timeout: FakeResponse(200, {"ok": False, "description": "unauthorized"}),
    )
    # Must not raise — a Telegram outage/misconfig must never break trade-plan generation.
    notify_trade_plan("tok", "chat123", "New trade plan: AAPL LONG")
