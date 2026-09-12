"""Insider (Form 4) scoring.

The load-bearing decision here is the asymmetry: buying scores, selling never
does. Executives sell constantly for diversification, taxes and pre-scheduled
10b5-1 plans — NVDA showed $653m of insider selling and zero buying over a
routine 90-day window. Scoring that as bearish would be scoring the payroll.
"""

from __future__ import annotations

from app.analysis.insider_scoring import (
    INSIDER_SCORE_CAP,
    MIN_NET_BUY_VALUE,
    score_insider_activity,
)
from app.data_providers.base import InsiderActivity


def _activity(**overrides) -> InsiderActivity:
    defaults = dict(
        symbol="ACME", window_days=90, buy_count=0, sell_count=0, buy_value=0.0, sell_value=0.0
    )
    defaults.update(overrides)
    return InsiderActivity(**defaults)


def test_meaningful_insider_buying_supports_a_long():
    activity = _activity(buy_count=3, buy_value=MIN_NET_BUY_VALUE * 4)
    score, reasons = score_insider_activity("long", activity)
    assert score == INSIDER_SCORE_CAP
    assert reasons and "insider purchase" in reasons[0]


def test_the_same_buying_contradicts_a_short():
    activity = _activity(buy_count=3, buy_value=MIN_NET_BUY_VALUE * 4)
    score, reasons = score_insider_activity("short", activity)
    assert score == -INSIDER_SCORE_CAP
    assert "against this short" in reasons[0]


def test_heavy_selling_is_never_penalised():
    """The whole point of the asymmetry. Routine selling must read as 0, not
    as a bearish signal, or almost every equity-compensating company is marked
    bearish almost all the time."""
    activity = _activity(sell_count=17, sell_value=653_000_000.0)
    assert score_insider_activity("long", activity) == (0, [])
    assert score_insider_activity("short", activity) == (0, [])


def test_buying_below_the_threshold_is_noise():
    activity = _activity(buy_count=1, buy_value=MIN_NET_BUY_VALUE / 10)
    assert score_insider_activity("long", activity) == (0, [])


def test_buying_swamped_by_selling_does_not_score():
    """net_value is what matters: a token purchase alongside a far larger sale
    is not a vote of confidence."""
    activity = _activity(buy_count=1, buy_value=50_000.0, sell_count=2, sell_value=5_000_000.0)
    assert score_insider_activity("long", activity) == (0, [])


def test_missing_activity_contributes_nothing():
    """Crypto, non-registrants and failed fetches are absences, not signals."""
    assert score_insider_activity("long", None) == (0, [])


def test_no_direction_contributes_nothing():
    activity = _activity(buy_count=5, buy_value=MIN_NET_BUY_VALUE * 10)
    assert score_insider_activity(None, activity) == (0, [])


def test_net_value_is_buys_minus_sells():
    activity = _activity(buy_value=1_000_000.0, sell_value=250_000.0)
    assert activity.net_value == 750_000.0
