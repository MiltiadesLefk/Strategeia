"""Credentials in the environment (.env) act as if they had been typed into Settings (config.seed_settings_from_env,
load_app_settings): they fill only what the saved settings leave empty, switch Finnhub on, and are saved so the
app can later switch them off."""

from __future__ import annotations

import json

import pytest

import app.config as config
from app.config import AppSettings, InfraSettings, seed_settings_from_env


def infra(**kw):
    values = {"finnhub_api_key": "", "telegram_bot_token": "", "telegram_chat_id": "", **kw}
    return InfraSettings(_env_file=None, **values)


def test_nothing_in_the_environment_changes_nothing():
    assert seed_settings_from_env(AppSettings(), infra()) == {}


def test_a_finnhub_key_fills_the_key_and_switches_finnhub_on():
    assert seed_settings_from_env(AppSettings(), infra(finnhub_api_key=" abc123 ")) == {
        "finnhub_api_key": "abc123", "finnhub_enabled": True,
    }


def test_telegram_token_and_chat_id_are_filled_independently():
    got = seed_settings_from_env(AppSettings(telegram_chat_id="42"), infra(telegram_bot_token="t", telegram_chat_id="99"))
    assert got == {"telegram_bot_token": "t"}


def test_a_value_already_in_settings_wins():
    saved = AppSettings(finnhub_api_key="mine", finnhub_enabled=False, telegram_bot_token="x", telegram_chat_id="1")
    assert seed_settings_from_env(saved, infra(finnhub_api_key="env", telegram_bot_token="y", telegram_chat_id="2")) == {}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    fake = InfraSettings(_env_file=None, finnhub_api_key="envkey", telegram_bot_token="tok", telegram_chat_id="7")
    monkeypatch.setattr(config, "get_infra_settings", lambda: fake)
    monkeypatch.setattr(config, "_settings_file", lambda: path)
    monkeypatch.setattr(config, "_settings_cache", None)
    yield path
    monkeypatch.setattr(config, "_settings_cache", None)


def test_first_load_seeds_and_saves_so_it_can_be_switched_off_later(isolated, monkeypatch):
    loaded = config.load_app_settings()
    assert loaded.finnhub_enabled and loaded.finnhub_api_key == "envkey"
    assert loaded.telegram_bot_token == "tok" and loaded.telegram_chat_id == "7"
    saved = json.loads(isolated.read_text(encoding="utf-8"))
    assert saved["finnhub_enabled"] is True and saved["finnhub_api_key"] == "envkey"

    # The user switches Finnhub off in the app; the next start keeps it off because the key is already saved.
    config.save_app_settings(loaded.model_copy(update={"finnhub_enabled": False}))
    monkeypatch.setattr(config, "_settings_cache", None)
    assert config.load_app_settings().finnhub_enabled is False
