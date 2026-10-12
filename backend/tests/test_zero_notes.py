"""A part that scores 0 says why (analysis/live_evidence.py, trade_plan_service._insider_zero_note), so the plan
card never has to say "this plan did not store which"."""

from __future__ import annotations

import pandas as pd

from app.analysis.live_evidence import evaluate_extra_evidence
from app.data_providers.base import InsiderActivity
from app.services.trade_plan_service import _insider_zero_note


def _bars(closes, volume=1_000):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": volume}, index=idx)


def test_rule_parts_that_find_nothing_explain_themselves():
    flat = _bars([100.0] * 120)
    ex = evaluate_extra_evidence("long", None, "XYZ", None, [], ohlcv=flat, market_ohlcv=flat)
    assert "gap of 0.0 points" in ex.notes["relative_strength"]
    assert "neither accumulation nor distribution" in ex.notes["volume_trend"] or "No volume" in ex.notes["volume_trend"]
    assert ex.notes["financial_health"] == "Needs two reported years of financials; 0 available."
    assert ex.notes["analyst_consensus"] == "No consensus EPS estimate for the next report."
    assert ex.notes["options_oi"] == "No usable options open-interest data for this symbol."


def test_missing_data_is_said_not_implied_to_be_neutral():
    ex = evaluate_extra_evidence("long", None, "XYZ", None, [], ohlcv=None, market_ohlcv=None)
    assert ex.notes["relative_strength"] == "Not enough price history for the stock or SPY to compare."


def test_insider_zero_note_carries_the_real_figures():
    note = _insider_zero_note(InsiderActivity("VST", 90, 3, 6, 1_173_069.25, 16_626_775.83), None)
    assert "$1,173,069" in note and "$16,626,776" in note and "3 purchases" in note and "6 sales" in note
