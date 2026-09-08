from __future__ import annotations

import pandas as pd
import pytest

from app.config import AppSettings
from app.data_providers.base import AllProvidersFailedError, DataProviderError, QuoteData
from app.services import health_monitor


class FakeUpProvider:
    name = "fake_up"

    def get_quote(self, symbol):
        return QuoteData(symbol=symbol, price=500.0, change_pct_24h=0.1, volume=1.0, avg_volume_20d=1.0)

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        raise NotImplementedError


class FakeDownProvider:
    name = "fake_down"

    def get_quote(self, symbol):
        raise AllProvidersFailedError("all providers failed")

    def get_ohlcv(self, symbol, period="6mo", interval="1d"):
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _reset_last_status():
    health_monitor._last_status = {}
    yield
    health_monitor._last_status = {}


def test_first_check_seeds_baseline_without_alerting(monkeypatch):
    calls = []
    monkeypatch.setattr(health_monitor, "notify", lambda token, chat_id, text: calls.append(text))
    settings = AppSettings(telegram_bot_token="tok", telegram_chat_id="chat")

    health_monitor.check_and_alert(settings, FakeDownProvider())

    assert calls == []
    assert health_monitor._last_status["market_data"] is False


def test_transition_to_offline_sends_one_alert(monkeypatch):
    calls = []
    monkeypatch.setattr(health_monitor, "notify", lambda token, chat_id, text: calls.append(text))
    settings = AppSettings(telegram_bot_token="tok", telegram_chat_id="chat")

    health_monitor.check_and_alert(settings, FakeUpProvider())  # baseline: online
    health_monitor.check_and_alert(settings, FakeDownProvider())  # transition: offline

    assert len(calls) == 1
    assert "offline" in calls[0].lower()


def test_transition_back_online_sends_recovery_alert(monkeypatch):
    calls = []
    monkeypatch.setattr(health_monitor, "notify", lambda token, chat_id, text: calls.append(text))
    settings = AppSettings(telegram_bot_token="tok", telegram_chat_id="chat")

    health_monitor.check_and_alert(settings, FakeDownProvider())  # baseline: offline
    health_monitor.check_and_alert(settings, FakeUpProvider())  # transition: online

    assert len(calls) == 1
    assert "back online" in calls[0].lower()


def test_steady_state_never_alerts(monkeypatch):
    calls = []
    monkeypatch.setattr(health_monitor, "notify", lambda token, chat_id, text: calls.append(text))
    settings = AppSettings(telegram_bot_token="tok", telegram_chat_id="chat")

    for _ in range(5):
        health_monitor.check_and_alert(settings, FakeUpProvider())

    assert calls == []


def test_health_check_failure_is_swallowed(monkeypatch):
    class BrokenProvider:
        name = "broken"

        def get_quote(self, symbol):
            raise DataProviderError("boom")  # not AllProvidersFailedError -> unexpected, should be caught broadly

    settings = AppSettings(telegram_bot_token="tok", telegram_chat_id="chat")

    result = health_monitor.check_and_alert(settings, BrokenProvider())

    assert result == {}
