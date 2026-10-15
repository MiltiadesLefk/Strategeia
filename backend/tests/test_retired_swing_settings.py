"""Swing trading is gone, but a settings.json written while it existed must still load."""
from __future__ import annotations

import json

from app import config
from app.config import AppSettings

RETIRED = {
    "max_holding_days": 12,
    "smart_money_max_holding_days": 90,
    "scale_out_enabled": True,
    "scale_out_fraction": 0.4,
    "scale_out_stop_mode": "trail",
    "scale_out_trail_r": 2.0,
    "ai_trading_overlay_enabled": True,
    "ai_overlay_scores_confidence": False,
    "ai_overlay_objection_action": "hold",
    "ai_overlay_vetoes_trade": False,
    "committee_gate_enabled": True,
    "committee_gate_action": "hold",
    "committee_gate_max_per_scan": 3,
    "ai_plan_take_enabled": False,
}


def test_an_old_settings_dict_with_the_retired_keys_still_builds():
    settings = AppSettings(**RETIRED, hold_stop_pct=30.0)
    assert settings.hold_stop_pct == 30.0
    for key in RETIRED:
        assert not hasattr(settings, key)


def test_an_old_settings_file_still_loads(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({**RETIRED, "hold_stop_pct": 30.0}), encoding="utf-8")
    monkeypatch.setattr(config, "_settings_file", lambda: path)
    monkeypatch.setattr(config, "_settings_cache", None)
    try:
        loaded = config.load_app_settings()
    finally:
        monkeypatch.setattr(config, "_settings_cache", None)
    assert loaded.hold_stop_pct == 30.0
